from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from check_gateway import check_once, expected_redirect, wait_for_gateway


class GatewayTests(unittest.TestCase):
    def wait(self, values, attempts):
        replies = iter(values)
        logs, sleeps = [], []
        result = wait_for_gateway(check=lambda: next(replies), attempts=attempts,
                                  sleep=sleeps.append, report=logs.append)
        return result, logs, sleeps

    def test_waits_for_old_nginx_404_before_accepting_login_redirect(self):
        ok, logs, sleeps = self.wait([(404, '', ''), (404, '', ''), (302, '/admin/login', '')], 3)
        self.assertTrue(ok)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(sum('HTTP 404' in line for line in logs), 1)

    def test_permanent_404_still_fails(self):
        ok, logs, sleeps = self.wait([(404, '', '')] * 3, 3)
        self.assertFalse(ok)
        self.assertEqual(len(sleeps), 2)
        self.assertIn('HTTP 404', logs[-2])

    def test_public_200_is_never_treated_as_protected(self):
        self.assertFalse(self.wait([(200, '', '')] * 2, 2)[0])

    def test_only_blog_login_redirect_is_accepted(self):
        self.assertTrue(expected_redirect(302, '/admin/login'))
        self.assertTrue(expected_redirect(302, 'https://stinkypuppy.duckdns.org/admin/login'))
        for location in ('https://evil.example/admin/login', '//evil.example/admin/login', 'http://stinkypuppy.duckdns.org/admin/login', '/admin/login?token=secret', '/workspace/', ''):
            self.assertFalse(expected_redirect(302, location))

    def test_transient_connection_failure_is_retried(self):
        ok, _, sleeps = self.wait([(0, '', 'curl 7'), (302, '/admin/login', '')], 2)
        self.assertTrue(ok)
        self.assertEqual(len(sleeps), 1)

    def test_probe_ignores_proxy_and_curlrc_and_keeps_tls_validation(self):
        def run(command, **kwargs):
            self.assertEqual(command[1], '--disable')
            self.assertEqual(command[command.index('--noproxy') + 1], '*')
            self.assertEqual(command[command.index('--resolve') + 1], 'stinkypuppy.duckdns.org:443:127.0.0.1')
            self.assertNotIn('--insecure', command)
            self.assertNotIn('--location', command)
            Path(command[command.index('--dump-header') + 1]).write_text('HTTP/1.1 302 Moved Temporarily\r\nLocation: /admin/login\r\n\r\n')
            return types.SimpleNamespace(returncode=0, stdout='302')
        with patch('check_gateway.subprocess.run', side_effect=run):
            self.assertEqual(check_once(), (302, '/admin/login', ''))

    def test_probe_reports_connection_error_without_raw_stderr(self):
        with patch('check_gateway.subprocess.run', return_value=types.SimpleNamespace(returncode=60, stdout='', stderr='unneeded private diagnostics')):
            self.assertEqual(check_once(), (0, '', 'curl 60'))

    def test_process_timeout_remains_failure(self):
        def timeout():
            raise subprocess.TimeoutExpired('curl', 6)
        logs = []
        self.assertFalse(wait_for_gateway(check=timeout, attempts=2, sleep=lambda _: None, report=logs.append))
        self.assertIn('connection unavailable', ' '.join(logs))


if __name__ == '__main__':
    unittest.main()
