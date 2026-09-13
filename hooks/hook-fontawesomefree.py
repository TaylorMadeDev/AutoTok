from PyInstaller.utils.hooks import collect_data_files


datas = collect_data_files(
    "fontawesomefree",
    includes=["static/fontawesomefree/webfonts/fa-solid-900.ttf"],
)
