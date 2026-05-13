from pos_report_bot.storage.filename import sanitize_windows_filename


def test_sanitize_windows_filename_replaces_forbidden_characters() -> None:
    value = sanitize_windows_filename('R02_每日/商品:銷售*明細?"<A>|.xls')

    assert value == "R02_每日_商品_銷售_明細___A__.xls"
    assert not any(char in value for char in '<>:"/\\|?*')


def test_sanitize_windows_filename_protects_reserved_names() -> None:
    assert sanitize_windows_filename("CON.xls") == "_CON.xls"
    assert sanitize_windows_filename("NUL") == "_NUL"


def test_sanitize_windows_filename_falls_back_when_empty() -> None:
    assert sanitize_windows_filename("///") == "report.xls"
