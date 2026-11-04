import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QUrl
    import desktop
except ImportError:
    desktop = None


@unittest.skipIf(desktop is None, 'Install requirements-desktop.txt for desktop checks')
class DesktopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = desktop.library.DATA
        desktop.library.DATA = Path(self.tmp.name)
        with desktop.library.db() as con:
            con.execute('INSERT INTO entries(title,url,"group",metadata) VALUES(?,?,?,?)', ('Example', 'https://example.com/movie.mp4', 'Movies', '{"year":"2026"}'))
        self.window = desktop.Window()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        desktop.library.DATA = self.original
        self.tmp.cleanup()

    def test_table_search_and_edit(self):
        self.assertEqual(self.window.table.rowCount(), 1)
        self.window.search.setText('missing')
        self.assertEqual(self.window.table.rowCount(), 0)
        self.window.search.clear()
        editor = desktop.Editor(self.window.rows[0], self.window)
        editor.fields['group'].setText('Favorites')
        editor.accept()
        self.window.refresh()
        self.assertEqual(self.window.rows[0]['group'], 'Favorites')

    def test_import_export_and_clear(self):
        source = Path(self.tmp.name) / 'input.m3u'
        source.write_text('#EXTINF:-1,New movie\nhttps://example.com/new.mp4\n')
        target = Path(self.tmp.name) / 'output.m3u'
        with patch.object(desktop.QFileDialog, 'getOpenFileName', return_value=(str(source), '')):
            self.window.import_playlist()
        self.assertEqual(len(self.window.entries), 2)
        with patch.object(desktop.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
            self.window.export_playlist()
        self.assertEqual(len(desktop.library.parse_m3u(target.read_text())), 2)
        saved = Path(self.tmp.name) / 'downloads'
        saved.mkdir()
        (saved / 'movie.mp4').write_bytes(b'keep')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.window.clear_library()
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertTrue((saved / 'movie.mp4').exists())

    def test_play_selects_native_media_source(self):
        self.window.table.selectRow(0)
        with patch.object(self.window.player, 'play') as play:
            self.window.play()
            play.assert_called_once()
        self.assertEqual(self.window.player.source(), QUrl('https://example.com/movie.mp4'))
        self.window.stop()
        self.assertTrue(self.window.player.source().isEmpty())
