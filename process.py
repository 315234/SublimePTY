#!coding: utf-8
from __future__ import division

import pty
import subprocess
from weakref import WeakValueDictionary
import pyte


class Supervisor(object):
    def __init__(self):
        self.processes = WeakValueDictionary()

    def register(self, process):
        self.processes[process.id] = process

    def process(self, process_id):
        if process_id in self.processes:
            return self.processes[process_id]
        return None

    def read_all(self):
        for process in self.processes.values():
            process._read()




class Process(object):
    DEFAULT_COLUMNS = 80
    DEFAULT_LINES   = 24

    def __init__(self, supervisor):
        from uuid import uuid4
        self.id = uuid4().hex
        self._supervisor = supervisor
        self._views = []
        self._columns = self.DEFAULT_COLUMNS
        self._lines = self.DEFAULT_LINES
        
        self._supervisor.register(self)

    def attach_view(self, view):
        """Connect a View(thing that displays Process output) to this Process"""
        self._views.append(view)
        view.process = self

    def detach_view(self, view):
        """Detaches a view that was previously added"""
        pass

    @property
    def columns(self):
        return self._columns

    @property
    def lines(self):
        return self._lines

    def available_columns(self):
        ac = self.DEFAULT_COLUMNS
        for v in self._views:
            ac = min(ac, v.available_columns())
        return ac

    def available_lines(self):
        al = self.DEFAULT_LINES
        for v in self._views:
            al = min(al, v.available_lines())
        return al

    def start(self):
        raise NotImplemented

    def stop(self):
        raise NotImplemented

    def is_running(self):
        raise NotImplemented

    def send_bytes(self, bytes):
        raise NotImplemented

    def send_keypress(self, key, ctrl=False, alt=False, shift=False, super=False):
        raise NotImplemented



class PtyProcess(Process):
    DEFAULT_LOCALE = 'en_US.UTF8'
    KEYMAP = {"enter": "\n", "tab": "\t", "f10": "\x1b[21~", 
              "space": " ",
              "f8": "\e[[19~", "escape": "\x1b\x1b", "down": "\x1b[B",
              "up": "\x1b[A", "right": "\x1b[C", "left": "\x1b[D",
              "backspace": "\b"}

    def __init__(self, supervisor, cmd=None, env=None, cwd=None):
        import select
        import os
        super(PtyProcess, self).__init__(supervisor)
        self._cmd = cmd or [os.environ["SHELL"]]
        # copy of whole env causes some problems
        self._env = env or {"TERM": "linux", 
                            'LOGNAME': os.environ["LOGNAME"],
                            'USER': os.environ["USER"],
                            "SHELL": os.environ["SHELL"],
                            "USERNAME": os.environ["USERNAME"],
                            "HOME": os.environ["HOME"], 
                            'COLUMNS': str(self.DEFAULT_COLUMNS), 
                            'LINES': str(self.DEFAULT_LINES), 
                            'LC_ALL': self.DEFAULT_LOCALE}

        self._cwd = cwd or "."
        self._process = None
        self._master = None
        self._slave = None
        self._poll = select.poll()

        self._stream = pyte.ByteStream()
        self._screens = {'diff': pyte.DiffScreen(self.DEFAULT_COLUMNS, self.DEFAULT_LINES)}
        for screen in self._screens.values():
            self._stream.attach(screen)


    def start(self):
        self._start()

    def _start(self):
        import select
        (self._master, self._slave) = pty.openpty()
        self._process = subprocess.Popen(self._cmd, stdin=self._slave, 
                                         stdout=self._slave, stderr=subprocess.STDOUT, 
                                         env=self._env, close_fds=True)
        self._poll.register(self._master, select.POLLIN)

    def refresh_views(self):
        sc = self._screens['diff']
        dis = sc.display
        lines_dict = dict((lineno, dis[lineno]) for lineno in sc.dirty)
        sc.dirty.clear()
        cursor = self._screens['diff'].cursor
        for v in self._views:
            v.diff_refresh(lines_dict, cursor)

    def _read(self):
        import os
        read = 0
        while True:
            if not self._poll.poll(0):
                break # no input
            data = os.read(self._master, 100)
            read += len(data)
            self._stream.feed(data)
        if read:
            self.refresh_views()
        return read

    def send_bytes(self, bytes):
        import os
        if bytes in self.KEYMAP:
            bytes = self.KEYMAP[bytes]
        os.write(self._master, bytes)

    def send_keypress(self, key, ctrl=False, alt=False, shift=False, super=False):
        self.send_bytes(key)

    def stop(self):
        self._process.kill()
        self._process = None 
        return 

    def is_running(self):
        return self._process is not None

    def send_keypress(self, key, ctrl=False, alt=False, shift=False, super=False):
        self.send_bytes(key)
        self._read()


class SublimeView(object):
    def __init__(self, view=None):
        v = view or self._new_view()
        self._view = v
        self._process = None
        
        v.settings().set("sublimepty", True)
        v.settings().set("line_numbers", False)
        v.settings().set("caret_style", "blink")
        v.settings().set("auto_complete", False)
        v.settings().set("draw_white_space", "none")
        #v.settings().set("color_scheme", "Packages/SublimePTY/SublimePTY.tmTheme")
        v.set_scratch(True)
        v.set_name("TERMINAL")

    @property
    def process(self):
        return self._process

    @process.setter
    def process(self, new_process):
        if self._process:
            self._process.detach_view(self)
        self._process = new_process
        if new_process:
            self._view.settings().set("sublimepty_id", new_process.id)
            self._fill_stars(new_process._columns, new_process._lines)
        else:
            self._view.settings().set("sublimepty_id", None)
            
    def _fill_stars(self, columns, lines):
        self.full_refresh(["*"*columns]*lines)

    def _new_view(self):
        import sublime
        return sublime.active_window().new_file()

    def available_columns(self):
        (w, h) = self._view.viewport_extent()
        return w // self._view.em_width()

    def available_lines(self):
        (w, h) = self._view.viewport_extent()
        return h // self._view.line_height()

    def _set_cursor(self, cursor):
        import sublime
        if not cursor:
            return 
        self._view.sel().clear()
        tp = self._view.text_point(cursor.y, cursor.x)
        self._view.sel().add(sublime.Region(tp, tp))
        
    def full_refresh(self, lines, cursor=None):
        import sublime
        v = self._view
        ed = v.begin_edit()
        whole = sublime.Region(0, v.size())
        v.erase(ed, whole)
        for idx in range(len(lines)):
            l = lines[idx]
            p = v.text_point(idx, 0)
            v.insert(ed, p, l + "\n")
        self._set_cursor(cursor)
        v.end_edit(ed)

    def diff_refresh(self, lines_dict, cursor=None):
        import sublime
        v = self._view
        ed = v.begin_edit()
        for lineno, text in lines_dict.items():
            p = v.text_point(lineno, 0)
            line_region = v.line(p)
            v.replace(ed, line_region, text)
        self._set_cursor(cursor)
        v.end_edit(ed)
