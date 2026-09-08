import csv
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


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

    def test_lap_time_board_is_top_10(self):
        rows = []
        for index in range(12):
            rows.append({
                'simulator_id': '1',
                'driver_name': f'Driver {index:02d}',
                'lap_time': str(80 + index),
                'email': '',
                'phone': '',
                'session_id': f'lap-session-{index}',
                'timestamp': f'2026-09-07T11:00:{index:02d}',
                'distance_pct': '100',
                'survey_answers': '{}',
            })
        self.write_rows(rows)

        board = receiver.read_leaderboard_entries(ranking_mode='lap_time')

        self.assertEqual(10, len(board))
        self.assertEqual('Driver 00', board[0]['driver_name'])
        self.assertEqual('Driver 09', board[-1]['driver_name'])

    def test_manual_cars_passed_board_updates_and_ranks_top_10(self):
        for index in range(12):
            receiver.save_cars_passed_result(
                f'Driver {index:02d}',
                index,
            )

        # A correction replaces the old value instead of retaining a previous
        # higher score, which is essential for operator-entered event results.
        receiver.save_cars_passed_result('driver 11', 3)
        board = receiver.read_leaderboard_entries(ranking_mode='cars_passed')

        self.assertEqual(10, len(board))
        self.assertEqual('Driver 10', board[0]['driver_name'])
        self.assertEqual(10, board[0]['cars_passed'])
        corrected = next(row for row in board if row['driver_name'].casefold() == 'driver 11')
        self.assertEqual(3, corrected['cars_passed'])
        self.assertEqual(len(board), len({row['driver_name'].casefold() for row in board}))
        self.assertFalse(Path('lap_times.csv').exists())
        self.assertTrue(receiver.remove_cars_passed_result('DRIVER 10'))
        self.assertFalse(receiver.remove_cars_passed_result('Missing Driver'))
        self.assertEqual(
            'Driver 09',
            receiver.read_leaderboard_entries(ranking_mode='cars_passed')[0]['driver_name'],
        )

    def test_manual_cars_passed_columns_match_event_request(self):
        app = QApplication.instance() or QApplication([])
        config = dict(receiver.DISPLAY_CONFIG_DEFAULTS)
        config['ranking_mode'] = 'cars_passed'
        window = receiver.LeaderboardWindow(config)
        try:
            headers, widths = window._column_layout()
            self.assertEqual(['POSITION', 'NAME', 'CARS PASSED'], headers)
            self.assertEqual(1728, sum(widths) + 120 + (2 * 40))
            self.assertEqual(10, window._max_entries())
            self.assertEqual(2464, window.leaderboard_panel.height())
            window.update_entries([{'driver_name': 'Jordan Lee', 'cars_passed': 27}])
            row_labels = window.entry_widgets[0].findChildren(QLabel)
            self.assertEqual(['1', 'Jordan Lee', '27'], [label.text() for label in row_labels])
        finally:
            window.close()
            app.processEvents()

    def test_manual_cars_passed_browser_endpoint(self):
        receiver.save_cars_passed_result('Alex Morgan', 14)
        receiver.save_cars_passed_result('Taylor Reed', 21)
        server = ThreadingHTTPServer(('127.0.0.1', 0), receiver.LapTimeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                    f'http://127.0.0.1:{server.server_port}/api/leaderboard?mode=cars_passed',
                    timeout=5) as response:
                payload = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual('cars_passed', payload['ranking_mode'])
        self.assertEqual('Taylor Reed', payload['entries'][0]['driver_name'])
        self.assertEqual(21, payload['entries'][0]['cars_passed'])

    def test_receiver_version_matches_installer(self):
        installer = (ROOT / 'installer.iss').read_text(encoding='utf-8')
        self.assertIn(f'AppVersion={receiver.APP_VERSION}', installer)

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
            self.assertEqual(1728, sum(widths) + 120 + (3 * 40))
            self.assertEqual(1728, window.leaderboard_panel.width())
            self.assertEqual(3136, window.leaderboard_panel.height())
            for label, width in zip(labels, widths):
                self.assertIsInstance(label, QLabel)
                self.assertLessEqual(
                    QFontMetrics(label.font()).horizontalAdvance(label.text()),
                    width - 12,
                )
        finally:
            window.close()
            app.processEvents()

    def test_vertical_board_scales_for_windows_200_percent(self):
        app = QApplication.instance() or QApplication([])
        config = dict(receiver.DISPLAY_CONFIG_DEFAULTS)
        window = receiver.LeaderboardWindow(config)
        try:
            window._vertical_canvas_scale = lambda: 0.5
            window.update_column_widths()
            headers, widths = window._column_layout()
            self.assertEqual(['POS', 'DRIVER', 'DISTANCE', 'TIME'], headers)
            self.assertEqual(864, sum(widths) + 60 + (3 * 20))
            self.assertEqual(864, window.leaderboard_panel.width())
            self.assertEqual(1568, window.leaderboard_panel.height())
            self.assertEqual(34, window._logical_offset(68))
        finally:
            window.close()
            app.processEvents()

    def test_browser_settings_editor_updates_only_valid_display_fields(self):
        config_path = str(Path(self.temp_dir.name) / 'config.json')
        with open(config_path, 'w', encoding='utf-8') as handle:
            json.dump({'server_port': 5432, 'vertical_panel_width': 1728}, handle)

        server = ThreadingHTTPServer(('127.0.0.1', 0), receiver.LapTimeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        base_url = f'http://127.0.0.1:{server.server_port}'

        with mock.patch.object(receiver, 'get_config_path', return_value=config_path):
            thread.start()
            try:
                with urllib.request.urlopen(f'{base_url}/leaderboard/settings', timeout=5) as response:
                    settings_html = response.read().decode('utf-8')

                payload = {'vertical_panel_width': 1600, 'vertical_row_height': 210}
                request = urllib.request.Request(
                    f'{base_url}/api/leaderboard/display-config',
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    saved = json.load(response)

                invalid_request = urllib.request.Request(
                    f'{base_url}/api/leaderboard/display-config',
                    data=json.dumps({'vertical_panel_width': 4000}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(invalid_request, timeout=5)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertIn('Leaderboard Display Settings', settings_html)
        self.assertEqual(1600, saved['config']['vertical_panel_width'])
        self.assertEqual(210, saved['config']['vertical_row_height'])
        self.assertEqual(400, raised.exception.code)
        with open(config_path, encoding='utf-8') as handle:
            persisted = json.load(handle)
        self.assertEqual(5432, persisted['server_port'])
        self.assertEqual(1600, persisted['vertical_panel_width'])

    def test_browser_board_uses_4k_portrait_design_canvas(self):
        html = receiver.LEADERBOARD_HTML_CONTENT
        self.assertIn('const baseWidth = vertical ? 2160 : 1920;', html)
        self.assertIn('const baseHeight = vertical ? 3840 : 1080;', html)
        self.assertIn('displayConfig.vertical_panel_width, 1728', html)
        self.assertIn("['POSITION', 'NAME', 'CARS PASSED']", html)
        self.assertIn("pinnedMode === 'cars_passed'", html)


if __name__ == '__main__':
    unittest.main(verbosity=2)
