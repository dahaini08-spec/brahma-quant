# stub: 转发到 archive 版本，避免循环导入
import importlib.util, sys, os

_archive_path = os.path.join(os.path.dirname(__file__), 'archive', 'sentiment_engine.py')
_spec = importlib.util.spec_from_file_location("_sentiment_engine_archive", _archive_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

analyze = _mod.analyze
_get_fg = _mod._get_fg
_get_fg_history = _mod._get_fg_history
