# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2014-2017 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Launcher for an external editor."""

import os
import tempfile

from PyQt5.QtCore import pyqtSignal, pyqtSlot, QObject, QProcess

from qutebrowser.config import config
from qutebrowser.utils import message, log
from qutebrowser.misc import guiprocess


class ExternalEditor(QObject):

    """Class to simplify editing a text in an external editor.

    Attributes:
        _text: The current text before the editor is opened.
        _filename: The name of the file to be edited.
        _remove_file: Whether the file should be removed when the editor is
                      closed.
        _proc: The GUIProcess of the editor.
    """

    editing_finished = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filename = None
        self._proc = None
        self._remove_file = None

    def _cleanup(self):
        """Clean up temporary files after the editor closed."""
        assert self._remove_file is not None
        if self._filename is None or not self._remove_file:
            # Could not create initial file.
            return

        try:
            if self._proc.exit_status() != QProcess.CrashExit:
                os.remove(self._filename)
        except OSError as e:
            # NOTE: Do not replace this with "raise CommandError" as it's
            # executed async.
            message.error("Failed to delete tempfile... ({})".format(e))

    @pyqtSlot(int, QProcess.ExitStatus)
    def on_proc_closed(self, exitcode, exitstatus):
        """Write the editor text into the form field and clean up tempfile.

        Callback for QProcess when the editor was closed.
        """
        log.procs.debug("Editor closed")
        if exitstatus != QProcess.NormalExit:
            # No error/cleanup here, since we already handle this in
            # on_proc_error.
            return
        try:
            if exitcode != 0:
                return
            encoding = config.val.editor.encoding
            try:
                with open(self._filename, 'r', encoding=encoding) as f:
                    text = f.read()
            except OSError as e:
                # NOTE: Do not replace this with "raise CommandError" as it's
                # executed async.
                message.error("Failed to read back edited file: {}".format(e))
                return
            log.procs.debug("Read back: {}".format(text))
            self.editing_finished.emit(text)
        finally:
            self._cleanup()

    @pyqtSlot(QProcess.ProcessError)
    def on_proc_error(self, _err):
        self._cleanup()

    def edit(self, text, caret_position=0):
        """Edit a given text.

        Args:
            text: The initial text to edit.
            caret_position: The position of the caret in the text.
        """
        if self._filename is not None:
            raise ValueError("Already editing a file!")
        try:
            # Close while the external process is running, as otherwise systems
            # with exclusive write access (e.g. Windows) may fail to update
            # the file from the external editor, see
            # https://github.com/qutebrowser/qutebrowser/issues/1767
            with tempfile.NamedTemporaryFile(
                    mode='w', prefix='qutebrowser-editor-',
                    encoding=config.val.editor.encoding,
                    delete=False) as fobj:
                if text:
                    fobj.write(text)
                self._filename = fobj.name
        except OSError as e:
            message.error("Failed to create initial file: {}".format(e))
            return

        self._remove_file = True

        # Here we calculate the line and column of the caret based on its
        # position and the given text.
        #
        # NOTE: Both line and column are 1-based indexes, because that's what
        # most editors use as line and column starting index.
        # By "most" we mean at least vim, nvim, gvim, emacs, atom, sublimetext,
        # notepad++, brackets, visual studio, QtCreator and so on.
        #
        # To find the line we just count how many newlines there are before
        # the caret and add 1.
        #
        # To find the column we calculate the difference between the caret and
        # the last newline before the caret.
        #
        # For example in the text `aaa\nbb|bbb` (| represents the caret):
        # caret_position = 6
        # text[:caret_position] = `aaa\nbb`
        # text[:caret_psotion].count('\n') = 1
        # caret_position - text[:caret_position].rfind('\n') = 3
        #
        # Thus line, column = 2, 3, and the caret is indeed in the second
        # line, third column
        line = text[:caret_position].count('\n') + 1
        column = caret_position - text[:caret_position].rfind('\n')
        self._start_editor(line=line, column=column)

    def edit_file(self, filename):
        """Edit the file with the given filename."""
        self._filename = filename
        self._remove_file = False
        self._start_editor()

    def _start_editor(self, line=1, column=1):
        """Start the editor with the file opened as self._filename.

        Args:
            caret_position: The position of the caret in the text.
        """
        self._proc = guiprocess.GUIProcess(what='editor', parent=self)
        self._proc.finished.connect(self.on_proc_closed)
        self._proc.error.connect(self.on_proc_error)
        editor = config.val.editor.command
        executable = editor[0]

        args = [self._sub_placeholder(arg, line, column) for arg in editor[1:]]
        log.procs.debug("Calling \"{}\" with args {}".format(executable, args))
        self._proc.start(executable, args)

    def _sub_placeholder(self, possible_placeholder, line, column):
        """Substitute a single placeholder.

        The input to this function is not guaranteed to be a valid or known
        placeholder. In this case the return value is the unchanged input.

        Args:
            possible_placeholder: an argument of editor.command.

        Return:
            The substituted placeholder or the original argument
        """
        sub = possible_placeholder\
            .replace('{}', self._filename)\
            .replace('{file}', self._filename)\
            .replace('{line}', str(line))\
            .replace('{line0}', str(line-1))\
            .replace('{column}', str(column))\
            .replace('{column0}', str(column-1))
        return sub
