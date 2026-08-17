import os
import tempfile

# До импорта app-модулей: изолированные БД и каталог загрузок
_tmp = tempfile.mkdtemp(prefix="aisc_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"  # недоступный адрес — LLM в тестах мокается
