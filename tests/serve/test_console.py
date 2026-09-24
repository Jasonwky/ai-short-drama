# -*- coding: utf-8 -*-
import pytest

from tools.serve.console import main


def test_db_and_data_root_are_required():
    with pytest.raises(SystemExit):
        main([])


def test_there_is_no_fake_backend_switch():
    # 正式入口连不上云机就如实显示连不上,不许静默切到假后端(CLAUDE.md fail-loud)
    with pytest.raises(SystemExit):
        main(["--db", "x.db", "--data-root", "d", "--fake"])
