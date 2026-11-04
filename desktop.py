"""Native M3uApp desktop application. No browser or web server is used."""
import json
from pathlib import Path
import shutil
import sys
import threading
import uuid

from PySide6.QtCore import Qt, QTimer, QUrl, QStandardPaths
from PySide6.QtGui import QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QFileDialog,
    QMessageBox, QDialog, QFormLayout, QTextEdit, QDialogButtonBox,
    QLabel, QSlider, QAbstractItemView,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
import app as library


def configure_storage():
    """Keep data outside application bundles and preserve the original library."""
    target = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    target.mkdir(parents=True, exist_ok=True)
    legacy = Path(__file__).parent / 'data'
    if not (target / 'library.sqlite3').exists() and (legacy / 'library.sqlite3').exists():
        # SQLite's backup API handles a database that was previously used in WAL mode.
        import sqlite3
        with sqlite3.connect(legacy / 'library.sqlite3') as source:
            with sqlite3.connect(target / 'library.sqlite3') as dest:
                source.backup(dest)
        if (legacy / 'downloads').exists():
            shutil.copytree(legacy / 'downloads', target / 'downloads', dirs_exist_ok=True)
    library.DATA = target


class Editor(QDialog):
    def __init__(self, entry, parent):
        super().__init__(parent)
        self.setWindowTitle('Edit movie or channel')
        self.resize(520, 420)
        layout = QFormLayout(self)
        self.fields = {}
        for key in ('title', 'group', 'url'):
            field = QLineEdit(entry[key])
            self.fields[key] = field
            layout.addRow(key.capitalize(), field)
        self.metadata = QTextEdit(json.dumps(entry['metadata'], indent=2))
        layout.addRow('Metadata (JSON)', self.metadata)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.entry = entry

    def accept(self):
        try:
            updated = dict(self.entry, **{key: field.text() for key, field in self.fields.items()}, metadata=json.loads(self.metadata.toPlainText()))
            library.validate_entry(updated)
            with library.db() as con:
                con.execute('UPDATE entries SET title=?,url=?,"group"=?,metadata=? WHERE id=?', (updated['title'], updated['url'], updated['group'], json.dumps(updated['metadata']), updated['id']))
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, 'Cannot save entry', str(error))
            return
        super().accept()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('M3uApp — Movies & TV')
        self.resize(1100, 850)
        self.rows = []
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
        for text, action in [('Import M3U', self.import_playlist), ('Export M3U', self.export_playlist), ('Clear library', self.clear_library), ('Open data folder', self.open_data)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search movies and channels…')
        self.search.textChanged.connect(self.render)
        self.groups = QComboBox()
        self.groups.currentIndexChanged.connect(self.render)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.groups)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Title', 'Group', 'Genre / Year'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 400)
        self.table.doubleClicked.connect(lambda _: self.play())
        layout.addWidget(self.table, 2)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        for text, action in [('▶ Play', self.play), ('Edit', self.edit), ('Download', self.download), ('Delete entry', self.delete)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            actions.addWidget(button)
        self.now_playing = QLabel('Select a movie or TV channel and click Play.')
        layout.addWidget(self.now_playing)
        self.video = QVideoWidget()
        self.escape = QShortcut(QKeySequence('Esc'), self.video)
        self.escape.activated.connect(lambda: self.video.setFullScreen(False))
        self.video.setMinimumHeight(220)
        layout.addWidget(self.video, 2)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.8)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.errorOccurred.connect(lambda *_: self.statusBar().showMessage('Playback failed: ' + self.player.errorString()))
        controls = QHBoxLayout()
        layout.addLayout(controls)
        for text, action in [('Play / Pause', self.toggle), ('Stop', self.stop), ('Fullscreen', self.fullscreen)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            controls.addWidget(button)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.durationChanged.connect(lambda value: self.seek.setRange(0, value))
        self.player.positionChanged.connect(self.update_position)
        controls.addWidget(self.seek, 1)
        controls.addWidget(QLabel('Volume'))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(80)
        volume.setMaximumWidth(100)
        volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        controls.addWidget(volume)
        self.jobs = QLabel('Downloads appear here. Files are saved in your app data folder.')
        self.jobs.setWordWrap(True)
        self.jobs.setTextFormat(Qt.TextFormat.PlainText)
        self.now_playing.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.jobs)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.refresh()

    def refresh(self):
        self.entries = library.entries()
        current = self.groups.currentText()
        self.groups.blockSignals(True)
        self.groups.clear()
        self.groups.addItem('All groups', None)
        for group in sorted({e['group'] for e in self.entries if e['group']}):
            self.groups.addItem(group, group)
        index = self.groups.findText(current)
        self.groups.setCurrentIndex(max(0, index))
        self.groups.blockSignals(False)
        self.render()

    def render(self, *_):
        group = self.groups.currentData()
        self.rows = [e for e in self.entries if self.search.text().casefold() in e['title'].casefold() and (group is None or group == e['group'])]
        self.table.setRowCount(len(self.rows))
        for row, entry in enumerate(self.rows):
            for column, value in enumerate([entry['title'], entry['group'], ' · '.join(entry['metadata'].get(k, '') for k in ('genre', 'year')).strip(' ·')]):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.statusBar().showMessage(f'{len(self.rows)} entries')

    def selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows):
            self.statusBar().showMessage('Select an entry first.')
            return None
        return self.rows[row]

    def import_playlist(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import playlist', '', 'M3U playlists (*.m3u *.m3u8);;All files (*)')
        if not path:
            return
        try:
            entries = library.parse_m3u(Path(path).read_text(encoding='utf-8-sig'))
            if not entries:
                raise ValueError('No entries found in this playlist.')
            with library.db() as con:
                for e in entries:
                    library.validate_entry(e)
                    con.execute('INSERT INTO entries(title,url,"group",metadata) VALUES(?,?,?,?)', (e['title'], e['url'], e['group'], json.dumps(e['metadata'])))
            self.refresh()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Import failed', str(error))

    def export_playlist(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export playlist', 'library.m3u', 'M3U playlist (*.m3u)')
        if path:
            try:
                Path(path).write_text(library.export_m3u(library.entries()), encoding='utf-8')
            except OSError as error:
                QMessageBox.warning(self, 'Export failed', str(error))

    def clear_library(self):
        if QMessageBox.question(self, 'Clear library?', 'Remove all playlist entries? Downloaded files will be kept.') == QMessageBox.StandardButton.Yes:
            self.stop()
            with library.db() as con:
                con.execute('DELETE FROM entries')
            self.search.clear()
            self.refresh()

    def edit(self):
        entry = self.selected()
        if entry and Editor(entry, self).exec():
            self.refresh()

    def delete(self):
        entry = self.selected()
        if entry and QMessageBox.question(self, 'Delete entry?', 'Remove this entry from the library?') == QMessageBox.StandardButton.Yes:
            with library.db() as con:
                con.execute('DELETE FROM entries WHERE id=?', (entry['id'],))
            self.refresh()

    def play(self):
        entry = self.selected()
        if not entry:
            return
        source = QUrl(entry['url'].split('|')[0].strip())
        if source.scheme() not in ('http', 'https') or not source.host():
            QMessageBox.warning(self, 'Cannot play', 'A valid HTTP or HTTPS media URL is required.')
            return
        self.player.stop()
        self.player.setSource(source)
        self.now_playing.setText('Now playing: ' + entry['title'])
        self.player.play()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.now_playing.setText('Playback stopped.')

    def fullscreen(self):
        self.video.setFullScreen(not self.video.isFullScreen())

    def update_position(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)

    def download(self):
        entry = self.selected()
        if not entry:
            return
        with library.LOCK:
            if sum(job['status'] == 'downloading' for job in library.JOBS.values()) >= 3:
                QMessageBox.information(self, 'Downloads busy', 'Wait for a download to finish (maximum three).')
                return
            job = uuid.uuid4().hex
            library.JOBS[job] = {'title': entry['title'], 'status': 'downloading', 'bytes': 0}
        threading.Thread(target=library.download, args=(job, entry), daemon=True).start()
        self.poll()

    def poll(self):
        with library.LOCK:
            jobs = [dict(job) for job in library.JOBS.values()]
        if jobs:
            self.jobs.setText('\n'.join(f"{j['title']}: {j['status']} · {j['bytes']/1048576:.1f} MB" + (' · ' + j['error'] if j.get('error') else '') for j in jobs))

    def open_data(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(library.DATA)))

    def closeEvent(self, event):
        with library.LOCK:
            active = any(j['status'] == 'downloading' for j in library.JOBS.values())
        if active and QMessageBox.question(self, 'Downloads running', 'Quit and interrupt running downloads?') != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self.player.stop()
        event.accept()


def main():
    application = QApplication(sys.argv)
    application.setOrganizationName('M3uApp')
    application.setApplicationName('M3uApp')
    if '--smoke-test' in sys.argv:
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        library.DATA = Path(temporary.name)
    else:
        configure_storage()
    window = Window()
    window.show()
    if '--smoke-test' in sys.argv:
        QTimer.singleShot(1000, application.quit)
    return application.exec()


if __name__ == '__main__':
    sys.exit(main())
