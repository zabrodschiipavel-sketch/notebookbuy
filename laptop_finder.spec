# laptop_finder.spec
# Запускать: pyinstaller laptop_finder.spec

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

# ---------------------------------------------------------------------------
# СКРЫТЫЕ ИМПОРТЫ
# Модули, которые PyInstaller не находит статически (динамический import,
# плагины, __import__-вызовы). Только реально нужные — без scipy/dask/polars.
# ---------------------------------------------------------------------------
hidden = (
    collect_submodules('streamlit')
    + collect_submodules('plotly')
    + collect_submodules('bs4')
    + collect_submodules('google.protobuf')   # protobuf нужен Streamlit
    + collect_submodules('anyio')
    + collect_submodules('starlette')
    + collect_submodules('uvicorn')
    + collect_submodules('altair')
    + collect_submodules('narwhals')
    + collect_submodules('pyarrow')
    + [
        # rapidfuzz: SIMD-варианты выбираются в рантайме — нужны все три
        'rapidfuzz',
        'rapidfuzz.fuzz',
        'rapidfuzz.process',
        'rapidfuzz.utils',
        'rapidfuzz.distance',
        'rapidfuzz.distance.Levenshtein',
        'rapidfuzz.distance.JaroWinkler',
        # rpds нужен jsonschema → altair; PyInstaller не видит C-расширение
        'rpds',
        # jsonschema validators и referencing
        'jsonschema',
        'jsonschema.validators',
        'jsonschema._types',
        'referencing',
        'referencing._core',
        'referencing.jsonschema',
        # pandas + sqlite
        'pandas',
        'pandas.io.formats.style',
        'sqlite3',
        'sqlite3.dump',
        # прочее
        'dotenv',
        'app_config',
        'db',
        'retry_utils',
        'parser',
        'benchmarks',
        'ai_service',
        'currency',
        'estimation',
        'openrouter',
        'web_search',
        'requests',
        'pkg_resources.py2_compat',
        'cryptography',
        'websockets',
        'websockets.legacy',
        'websockets.legacy.server',
        'click',
        'gitdb',
        'git',
    ]
)

# ---------------------------------------------------------------------------
# МЕТАДАННЫЕ ПАКЕТОВ (dist-info)
# Нужны для importlib.metadata.version() — без них Streamlit падает при старте
# с PackageNotFoundError. copy_metadata копирует *.dist-info в сборку.
# ---------------------------------------------------------------------------
metadata = (
    copy_metadata('streamlit')
    + copy_metadata('altair')
    + copy_metadata('plotly')
    + copy_metadata('pyarrow')
    + copy_metadata('narwhals')
    + copy_metadata('anyio')
    + copy_metadata('starlette')
    + copy_metadata('uvicorn')
    + copy_metadata('click')
    + copy_metadata('requests')
    + copy_metadata('pandas')
    + copy_metadata('rapidfuzz')
)

# ---------------------------------------------------------------------------
# ДАННЫЕ (статика, шаблоны, схемы)
# ---------------------------------------------------------------------------
datas = (
    collect_data_files('streamlit', include_py_files=True)
    + collect_data_files('plotly')
    + collect_data_files('altair')
    + collect_data_files('pyarrow')
    + metadata   # <-- dist-info добавлены сюда
    + [
        ('laptop_dashboard.py',   '.'),
        ('laptop_analyzer_v3.py', '.'),
        ('scoring.py',            '.'),
        ('lappars.py',            '.'),
        ('db.py',                 '.'),
        ('app_config.py',         '.'),
        ('retry_utils.py',        '.'),
        ('parser.py',             '.'),
        ('benchmarks.py',         '.'),
        ('ai_service.py',         '.'),
        ('currency.py',           '.'),
        # Дашборд импортирует estimation, а он читает components_db.json —
        # без них .exe падал на старте с ModuleNotFoundError.
        ('estimation.py',         '.'),
        ('openrouter.py',         '.'),
        ('web_search.py',         '.'),
        ('components_db.json',    '.'),
        ('query_999.graphql',     '.'),
        ('passmark_cpu.json',     '.'),
        ('passmark_gpu.json',     '.'),
    ]
)

# ---------------------------------------------------------------------------
# ИСКЛЮЧЕНИЯ — убираем тяжёлые пакеты, которые проект не использует
# Экономит 50–100 МБ от итогового размера .exe
# ---------------------------------------------------------------------------
EXCLUDES = [
    # Графика / визуализация (не используется)
    'matplotlib', 'PIL', 'Pillow',
    # Тяжёлые ML-фреймворки
    'tensorflow', 'torch', 'sklearn', 'scipy',
    # DataFrame-альтернативы (есть только pandas)
    'polars', 'dask', 'modin', 'cudf', 'pyspark', 'ibis', 'duckdb',
    # GUI-тулкиты
    'tkinter', 'PyQt5', 'PyQt6', 'wx', 'PySide2', 'PySide6',
    # Jupyter / IPython
    'IPython', 'ipykernel', 'ipywidgets', 'nbformat',
    # Snowflake / облачные коннекторы (не нужны)
    'snowflake', 'sqlalchemy',
    # Тестовые фреймворки (pytest и т.д.)
    '_pytest', 'pytest',
    # Прочий балласт
    'numba', 'numexpr', 'openpyxl', 'xlrd', 'lxml',
    'statsmodels', 'skimage', 'xarray', 'geopandas',
]

# ---------------------------------------------------------------------------
# СБОРКА
# ---------------------------------------------------------------------------
a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LaptopFinder999',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,        # Убрать если UPX не установлен (https://upx.github.io)
    upx_exclude=[
        # pyarrow и rapidfuzz — скомпилированные .pyd, UPX их иногда ломает
        'pyarrow*.pyd',
        'rapidfuzz*.pyd',
    ],
    runtime_tmpdir=None,
    console=True,    # False = убрать консольное окно (тогда ошибки не видны!)
    icon=None,
    onefile=True,
)
