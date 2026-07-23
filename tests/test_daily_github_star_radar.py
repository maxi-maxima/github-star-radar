import gzip
import io
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

import daily_github_star_radar as radar


class StarRadarUnitTest(unittest.TestCase):
    def test_hour_range_and_url_format(self):
        end = datetime(2026, 7, 23, 15, tzinfo=timezone.utc)
        hours = radar.hour_range(end, 3)
        self.assertEqual([hour.hour for hour in hours], [12, 13, 14])
        self.assertEqual(radar.gharchive_url(hours[0]), 'https://data.gharchive.org/2026-07-23-12.json.gz')

    def test_collect_star_candidates_deduplicates_repo_actor_pairs(self):
        events = [
            {'type': 'WatchEvent', 'payload': {'action': 'started'}, 'repo': {'id': 1, 'name': 'owner/repo'}, 'actor': {'id': 10}},
            {'type': 'WatchEvent', 'payload': {'action': 'started'}, 'repo': {'id': 1, 'name': 'owner/repo'}, 'actor': {'id': 10}},
            {'type': 'WatchEvent', 'payload': {'action': 'started'}, 'repo': {'id': 1, 'name': 'owner/repo'}, 'actor': {'id': 11}},
            {'type': 'IssuesEvent', 'repo': {'id': 2, 'name': 'owner/other'}, 'actor': {'id': 12}},
        ]
        with mock.patch.object(radar, 'iter_events_for_hour', return_value=iter(events)):
            candidates, total = radar.collect_star_candidates([datetime(2026, 7, 23, tzinfo=timezone.utc)])
        self.assertEqual(total, 2)
        self.assertEqual(candidates, [radar.RepoCandidate(repo_id=1, repo_name='owner/repo', delta_stars=2)])

    def test_iter_events_for_hour_skips_invalid_json(self):
        payload = b'{"type":"WatchEvent"}\nnot-json\n{"type":"IssuesEvent"}\n'
        gzipped = io.BytesIO()
        with gzip.GzipFile(fileobj=gzipped, mode='wb') as gz:
            gz.write(payload)
        gzipped.seek(0)

        response = mock.Mock()
        response.status_code = 200
        response.raw = gzipped
        response.raise_for_status.return_value = None
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(radar.requests, 'get', return_value=response):
            events = list(radar.iter_events_for_hour(datetime(2026, 7, 23, tzinfo=timezone.utc)))
        self.assertEqual([event['type'] for event in events], ['WatchEvent', 'IssuesEvent'])

    def test_zero_hour_window_smoke_path(self):
        with mock.patch.dict('os.environ', {'WINDOW_HOURS': '0', 'TOP_N': '1', 'OUTPUT_DIR': 'test-smoke-reports'}):
            with mock.patch.object(radar, 'datetime') as fake_datetime:
                now = datetime(2026, 7, 23, 15, 30, tzinfo=timezone.utc)
                fake_datetime.now.return_value = now
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                with mock.patch.object(radar.Path, 'write_text') as write_text:
                    with mock.patch.object(radar.Path, 'mkdir'):
                        radar.main()
        self.assertTrue(write_text.called)


if __name__ == '__main__':
    unittest.main()
