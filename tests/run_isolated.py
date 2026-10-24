"""在临时数据库和工作目录中运行回归，避免导入旧测试时触碰运行数据。"""

from contextlib import ExitStack, chdir
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from cryptography.fernet import Fernet
from loguru import logger


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    with ExitStack() as isolation:
        directory = isolation.enter_context(tempfile.TemporaryDirectory(prefix='xianyu-tests-'))
        isolation.enter_context(chdir(directory))
        isolation.enter_context(mock.patch.dict(os.environ, {
            'DB_PATH': str(Path(directory) / 'tests.db'),
            'SECRET_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'SQL_LOG_ENABLED': 'false',
        }))
        isolation.enter_context(mock.patch.object(logger, '_log'))
        isolation.enter_context(mock.patch('file_log_collector.setup_file_logging'))
        suite = unittest.defaultTestLoader.discover(str(repo / 'tests'), pattern=sys.argv[1] if len(sys.argv) > 1 else 'test_*.py')
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        if 'db_manager' in sys.modules:
            sys.modules['db_manager'].db_manager.close()
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
