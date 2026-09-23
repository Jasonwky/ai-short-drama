# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# 让测试能 `import tools.xxx`(tools 是命名空间包,仓库根目录要在 sys.path 上)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
