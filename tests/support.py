"""供数据库回归复用的隔离夹具；保留其他模块已经加载的全局数据库。"""

from contextlib import ExitStack
import importlib
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

from cryptography.fernet import Fernet
from loguru import logger


class IsolatedDatabaseMixin:
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        directory = cls.resources.enter_context(tempfile.TemporaryDirectory())
        cls.resources.enter_context(mock.patch.dict(os.environ, {
            'DB_PATH': str(Path(directory) / 'bootstrap.db'),
            'SECRET_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'SQL_LOG_ENABLED': 'false',
        }))
        cls.resources.enter_context(mock.patch.object(logger, '_log'))
        already_loaded = 'db_manager' in sys.modules
        module = importlib.import_module('db_manager')
        if not already_loaded:
            cls.resources.callback(module.db_manager.close)
        cls.database_type = module.DBManager
        cls.db = module.DBManager(str(Path(directory) / 'fixtures.db'))
        cls.resources.callback(cls.db.close)
