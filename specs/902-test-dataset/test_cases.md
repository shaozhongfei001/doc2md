# Test Cases: 902 测试样本集

| 编号 | 场景 | 输入 | 预期 | 对应验收 |
|---|---|---|---|---|
| TC001 | fixture 清单 | `backend/tests/fixtures/sample_dataset/` | spec §3.1 文件均存在 | A002 |
| TC002 | 中文路径 | `中文路径/银行项目/方案.txt` | 路径可打开，UTF-8 内容可读 | A003 |
| TC003 | 精确重复 | `方案.txt` 与 `方案副本.txt` | sha256 相同且非空 | A004 |
| TC004 | 互异内容 | md/csv/html/json/docx/pdf | 两两 sha256 不同 | A005 |
| TC005 | PDF 头 | `pdf/hello.pdf` | 文件以 `%PDF-` 开头 | A006 |
| TC006 | docx zip | `office_like/最小文档.docx` | 为 zip（`PK` 头）且含 `word/document.xml` | A006 |
| TC007 | manifest | `manifest.yaml` | 每个 rel_path 存在；重复对标记正确 | A002 |
| TC008 | 敏感扫描 | 样本文本 + manifest | 无 `BEGIN PRIVATE KEY`、无 `password=` 赋值 | A007 |
| TC009 | 既有 fixture | `fixtures/中文路径/银行项目/方案.txt` | 内容仍为既有字节（与 git HEAD 或锁定哈希一致） | A008 |
| TC010 | 脚本首次 | 清空 `data/sample_dataset/` 后运行脚本 | 副本与 fixture 哈希一致；exit 0 | A009 A010 |
| TC011 | 脚本重跑 | 再运行一次 | 哈希不变；无多余删除 | A009 |
| TC012 | 无 DB | 测试过程 | 无 pymysql connect / 无 SQLAlchemy engine | A011 |
| TC013 | 无 parser | 测试过程 | 不 import 解析 service，不 spawn `magic-pdf` | A012 |
| TC014 | 逃逸路径 | manifest 若被恶意写成 `../raw_vault/...` | 脚本拒绝（若脚本读 manifest 复制） | A010 |
| TC015 | 中文内容 | txt/md | 含中文句子，非空 | A003 |
