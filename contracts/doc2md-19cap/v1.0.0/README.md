# 机器可读契约

状态 DESIGN_CANDIDATE。这些是新伴随产物的候选合同，不替换 document-ir/1.0 或三件套。
contracts/contract_models.py 是契约参考模型；schemas/ 是从同一模型生成的 JSON Schema；
examples/ 全部为合成数据。模型不包含解析器、模型推理、真实权限、持久化或银行连接器实现。

闭合字段和枚举由 schema 约束；来源授权、路径实际存在、资产实际 hash、模型输出引用真实性、
补丁审批、发布与 checkpoint 的持久性仍由 SDD 指定的服务控制点验证。不能只过 schema 就发布。

使用项目隔离 Python 环境运行：
```bash
python tools/check_delivery.py
python tools/check_baseline.py --repo /absolute/path/to/doc2md
```

check_delivery 依赖 Pydantic 2.x。它仅验证交付包结构/示例/关联和几项负例；
不会执行生产测试、OCR/VLM、银行接口或独立 QA，也不会安装依赖。
