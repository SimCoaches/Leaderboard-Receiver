import csv
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Client'))

import gui_client_qt as receiver
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QLabel


class ReceiverReleaseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.temp_dir = tempfile.TemporaryDirectory()
        os.chdir(self.temp_dir.name)
        receiver.queue_data = {
            'queue': [], 'session_history': [], 'active_sessions': {},
            'last_updated': 0,
        }
        receiver.peer_discovery = None

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.temp_dir.cleanup()

    @staticmethod
    def write_rows(rows):
        with open('lap_times.csv', 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=receiver.LAP_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_distance_board_keeps_best_run_tiebreaks_and_limits_to_13(self):
        rows = []
        for index in range(15):
            rows.append({
                'simulator_id': '1',
                'driver_name': f'Driver {index:02d}',
                'lap_time': str(200 - index),
                'email': '',
                'phone': '',
                'session_id': f'session-{index}',
                'timestamp': f'2026-09-07T10:00:{index:02d}',
                'distance_pct': str(99 - index),
                'survey_answers': '{}',
            })
        rows.extend([
            {
                **rows[0], 'lap_time': '150', 'session_id': 'driver-zero-better',
                'distance_pct': '99.5',
            },
            {
                **rows[1], 'lap_time': '100', 'session_id': 'driver-one-tie',
                'distance_pct': '99.5',
            },
        ])
        self.write_rows(rows)

        board = receiver.read_leaderboard_entries(ranking_mode='distance')

        self.assertEqual(13, len(board))
        self.assertEqual('Driver 01', board[0]['driver_name'])
        self.assertEqual('Driver 00', board[1]['driver_name'])
        self.assertEqual(99.5, board[0]['distance_pct'])
        self.assertLess(board[0]['lap_time'], board[1]['lap_time'])
        self.assertEqual(len(board), len({row['driver_name'] for row in board}))

    def test_old_csv_migrates_without_losing_distance(self):
        with open('lap_times.csv', 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                'simulator_id', 'driver_name', 'lap_time', 'email', 'phone',
                'timestamp', 'distance_pct',
            ])
            writer.writeheader()
            writer.writerow({
                'simulator_id': '2', 'driver_name': 'Legacy Driver',
                'lap_time': '77.123', 'email': '', 'phone': '',
                'timestamp': '2026-09-07T09:00:00', 'distance_pct': '84.67',
            })

        receiver.migrate_lap_times_csv()

        with open('lap_times.csv', newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(receiver.LAP_CSV_FIELDS, list(rows[0].keys()))
        self.assertEqual('84.67', rows[0]['distance_pct'])
        self.assertEqual('', rows[0]['session_id'])
        self.assertEqual('{}', rows[0]['survey_answers'])

    def test_http_post_preserves_registration_data_and_deduplicates_retry(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), receiver.LapTimeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        payload = {
            'simulator_id': 3,
            'driver_name': 'Jane Driver',
            'lap_time': 91.234,
            'session_id': 'event-session-123',
            'email': 'jane@example.com',
            'phone': '+12085551234',
            'distance_pct': 87.654321,
            'survey_answers': {'Favorite car': 'Porsche'},
        }

        try:
            responses = []
            for _ in range(2):
                request = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    responses.append(response.status)

            with urllib.request.urlopen(
                    f'{url}/api/leaderboard?mode=distance', timeout=5) as response:
                board = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual([200, 200], responses)
        with open('lap_times.csv', newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(1, len(rows))
        self.assertEqual('event-session-123', rows[0]['session_id'])
        self.assertEqual('87.654321', rows[0]['distance_pct'])
        self.assertEqual({'Favorite car': 'Porsche'}, json.loads(rows[0]['survey_answers']))
        self.assertEqual('distance', board['ranking_mode'])
        self.assertEqual(87.654321, board['entries'][0]['distance_pct'])

    def test_vertical_distance_header_is_complete_and_fits(self):
        app = QApplication.instance() or QApplication([])
        config = dict(receiver.DISPLAY_CONFIG_DEFAULTS)
        config.update({
            'ranking_mode': 'distance',
            'orientation': 'vertical',
            'header_font_size': 48,
        })
        window = receiver.LeaderboardWindow(config)
        try:
            headers, widths = window._column_layout()
            labels = [
                window.header_widget.layout().itemAt(index).widget()
                for index in range(window.header_widget.layout().count())
            ]
            self.assertEqual(['POS', 'DRIVER', 'DISTANCE', 'TIME'], headers)
            self.assertEqual(864, sum(widths) + 60 + (3 * 20))
            for label, width in zip(labels, widths):
                self.assertIsInstance(label, QLabel)
                self.assertLessEqual(
                    QFontMetrics(label.font()).horizontalAdvance(label.text()),
                    width - 12,
                )
        finally:
            window.close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main(verbosity=2)
