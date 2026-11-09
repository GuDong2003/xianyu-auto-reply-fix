import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    'container_smoke', Path(__file__).resolve().parents[1] / '.github/scripts/container_smoke.py',
)
container_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(container_smoke)


class ContainerSmokeTests(unittest.TestCase):
    def setUp(self):
        self.real_run_docker = container_smoke.run_docker
        self.docker = self.enterContext(mock.patch.object(container_smoke, 'run_docker'))
        self.docker.return_value = subprocess.CompletedProcess([], 0, stdout='true\n', stderr='')
        self.enterContext(mock.patch('builtins.print'))

    def test_success_uses_default_entrypoint_and_removes_only_its_temporary_container(self):
        with mock.patch.object(container_smoke, 'wait_for_health') as health:
            container_smoke.smoke('synthetic-image')
        runtime, startup, cleanup = self.docker.call_args_list
        self.assertIn('--entrypoint', runtime.args)
        self.assertNotIn('--entrypoint', startup.args)
        self.assertNotIn('--volume', startup.args)
        self.assertEqual(startup.args[startup.args.index('--network') + 1], 'none')
        name = startup.args[startup.args.index('--name') + 1]
        self.assertTrue(name.startswith('xianyu-smoke-'))
        health.assert_called_once_with(name, 120)
        self.assertEqual(cleanup.args, ('rm', '--force', name))
        credentials = startup.kwargs['env']
        self.assertTrue(all(
            len(credentials[key]) >= 32 and credentials[key] not in repr(startup.args)
            for key in ('ADMIN_PASSWORD', 'JWT_SECRET_KEY', 'SECRET_ENCRYPTION_KEY')
        ), 'Pass generated credentials using the environment, not command-line values')

    def test_runtime_failure_prevents_starting_the_application(self):
        self.docker.side_effect = container_smoke.SmokeFailure('runtime check failed')
        with self.assertRaises(container_smoke.SmokeFailure):
            container_smoke.smoke('synthetic-image')
        self.docker.assert_called_once()

    def test_failed_startup_still_removes_the_temporary_container(self):
        with mock.patch.object(container_smoke, 'wait_for_health', side_effect=container_smoke.SmokeFailure('startup failed')):
            with self.assertRaises(container_smoke.SmokeFailure):
                container_smoke.smoke('synthetic-image')
        self.assertEqual(self.docker.call_args.args[:2], ('rm', '--force'))

    def test_health_probe_retries_until_healthy(self):
        probes = iter([1, 1, 0])

        def docker(*args, **kwargs):
            if args[0] == 'inspect':
                return subprocess.CompletedProcess([], 0, stdout='true\n')
            return subprocess.CompletedProcess([], next(probes))

        self.docker.side_effect = docker
        with mock.patch.object(container_smoke.time, 'sleep') as sleep:
            container_smoke.wait_for_health('synthetic-container', 120)
        self.assertEqual(sum(call.args[0] == 'exec' for call in self.docker.call_args_list), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_stopped_container_fails_before_any_health_probe(self):
        self.docker.return_value.stdout = 'false\n'
        with self.assertRaisesRegex(container_smoke.SmokeFailure, 'exited'):
            container_smoke.wait_for_health('synthetic-container', 120)
        self.docker.assert_called_once()

    def test_health_deadline_does_not_wait_forever(self):
        self.docker.side_effect = [
            subprocess.CompletedProcess([], 0, stdout='true\n'),
            subprocess.CompletedProcess([], 1),
        ]
        with mock.patch.object(container_smoke.time, 'monotonic', side_effect=[0, 0, 121]), \
                mock.patch.object(container_smoke.time, 'sleep'):
            with self.assertRaisesRegex(container_smoke.SmokeFailure, 'deadline'):
                container_smoke.wait_for_health('synthetic-container', 120)

    def test_subprocess_failure_does_not_expose_captured_credentials(self):
        # 调用真实封装，保持 Docker 命令本身为模拟。
        with mock.patch.object(container_smoke.subprocess, 'run', return_value=subprocess.CompletedProcess(
            [], 1, stdout='synthetic-private-output', stderr='synthetic-private-error',
        )):
            with self.assertRaises(container_smoke.SmokeFailure) as result:
                self.real_run_docker('run', 'synthetic-image')
        self.assertNotIn('synthetic-private', str(result.exception))


if __name__ == '__main__':
    unittest.main()
