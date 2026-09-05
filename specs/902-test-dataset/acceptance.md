# Acceptance: 902 测试样本集

> Spec: `specs/902-test-dataset/`  
> 对照：`spec.md`、`test_cases.md`

| ID | 标准 | 通过条件 |
|---|---|---|
| A001 | 活跃 Spec 对齐 | 只实现 902；无 review / docker / parser / 001–013 重开 |
| A002 | 样本清单完整 | spec §3.1 列出的文件均存在于 `backend/tests/fixtures/sample_dataset/` |
| A003 | 中文路径 | 至少一层中文目录 + 中文文件名可读 |
| A004 | 精确重复 | `方案.txt` 与 `方案副本.txt` sha256 相同 |
| A005 | 非重复互异 | 其余 MVP 文件 sha256 均不同 |
| A006 | 路由覆盖文件 | 存在 `.docx` 与 `.pdf` 各至少 1 个 |
| A007 | 无敏感内容 | 样本与 manifest 不含密码、私钥、真实证件号 |
| A008 | 既有 fixture 只读 | `backend/tests/fixtures/中文路径/银行项目/` 两文件字节未被 902 改动 |
| A009 | 脚本幂等 | `create_sample_dataset.sh` 连续两次，目标文件哈希不变 |
| A010 | 写路径收敛 | 脚本只写 fixture 树与 `data/sample_dataset/` |
| A011 | 零数据库 | 实现与测试不连接 MySQL |
| A012 | 零 parser | 不调用 MarkItDown / MinerU / `magic-pdf` |
| A013 | 无业务代码 | `backend/app/**` 无 902 diff |
| A014 | 原始文件安全 | 不移动 / 删除 / 重命名 / 覆盖用户原始文件 |
| A015 | 文档 | `docs/sample_dataset.md` 含生成命令与禁止事项，无密钥 |
| A016 | 测试 | `test_sample_dataset.py` 通过；未破坏既有测试意图 |

**总体通过：** 上表全部 PASS 或 PASS WITH NOTES（notes 不得否定 A001/A011/A012/A013/A014）。
