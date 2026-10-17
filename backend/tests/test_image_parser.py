"""015 图片解析流水线（image parse pipeline）测试。

测试先行（plan §9.3）：本文件先于实现落盘，实现前的失败即「红」基线。

反假绿设计（O-3 / D-2）：
- **引擎永不真调**：PaddleOCR 与 VLM 全部经注入的假客户端驱动；本机无 paddleocr 也必须全绿。
- **D-2 变异锚点**：`test_mock_result_is_never_persisted_with_forged_text` 断言 mock 结果
  零落盘；若实现里去掉 `result.mock` 拒绝分支，该用例必然 FAIL（见 A015-06）。
- **无 MySQL 也跑主链路**：`ImageParserService(candidates_loader=...)` 是可注入测试缝，
  使 plan/execute/persist/report 全链路可在 tmp_path 上端到端验证；
  仅「DB 候选加载」本身需要 MySQL，其用例在不可用时 `pytest.skip`（不改判定）。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from typer.testing import CliRunner

from app.adapters import image_preprocess, image_quality, ocr_paddle
from app.adapters.image_preprocess import preprocess_image
from app.adapters.image_quality import ImageQualityResult, score_image
from app.adapters.ocr_paddle import EngineDependencyMissing, check_paddleocr_available
from app.adapters.vlm_adapter import MockVlmClient, OcrResult, VlmClient, build_vlm_client
from app.cli.main import app
from app.core.config import load_config
from app.core.vault_paths import build_vault_artifact_paths, build_vault_dir
from app.services.image_parser import (
    DECISION_ALREADY_SUCCESS,
    DECISION_DEPENDENCY_MISSING,
    DECISION_ENGINE_ERROR,
    DECISION_PARSED,
    DECISION_ROUTE_MISMATCH,
    DECISION_TIMEOUT,
    DECISION_UNSUPPORTED_WITH_HINT,
    ENGINE_AUTO,
    ENGINE_PADDLEOCR,
    ENGINE_VLM,
    PARSER_ADAPTER_VERSION,
    PARSER_NAME,
    PARSER_PROFILE_PADDLEOCR,
    PARSER_PROFILE_VLM,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    ImageParseConfig,
    ImageParserService,
    ParseCandidate,
    build_image_parsed_paths,
    load_image_parse_config,
)
from tests._db_env import mysql_config_block

CLEAR_TEXT = "图像解析流水线 Invoice No. 2026-001"
BLUR_RADIUS = 10.0


# --------------------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------------------
def _make_text_image(
    path: Path,
    *,
    blur: float = 0.0,
    text: str = CLEAR_TEXT,
) -> Path:
    """现场用 Pillow 生成样本图（不提交二进制）。blur=0 → 清晰；blur=10 → 低质。"""
    image = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:  # pragma: no cover - 老版本 Pillow
        font = ImageFont.load_default()
    draw.text((30, 60), text, fill="black", font=font)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    path.parent.mkdir(parents=True, exist_ok=True)
    # 固定 format：候选夹具会用 `.txt` 等非图片后缀表达「扩展名与内容不符」，
    # Pillow 若按后缀推断格式会直接抛错。
    image.save(path, format="PNG")
    return path


def _write_test_config(config_path: Path, workspace_root: Path) -> None:
    config_path.write_text(
        f"""app:
  name: personal-kb
  timezone: Asia/Shanghai
  pipeline_version: v1.1

storage:
  source_registry_root: {workspace_root / "source_registry"}
  raw_vault_root: {workspace_root / "raw_vault"}
  parsed_root: {workspace_root / "parsed"}
  curated_root: {workspace_root / "curated"}
  quarantine_root: {workspace_root / "quarantine"}
  reports_root: {workspace_root / "reports"}

mysql:
{mysql_config_block()}

raw:
  original_files_readonly: true
  copy_unique_content_to_vault: true
""",
        encoding="utf-8",
    )

@dataclass
class _Env:
    workspace: Path
    config_path: Path
    config: Any
    raw_vault_root: Path
    parsed_root: Path
    reports_root: Path


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    workspace = tmp_path / "workspace"
    config_path = tmp_path / "app.yaml"
    _write_test_config(config_path, workspace)
    return _Env(
        workspace=workspace,
        config_path=config_path,
        config=load_config(config_path),
        raw_vault_root=workspace / "raw_vault",
        parsed_root=workspace / "parsed",
        reports_root=workspace / "reports",
    )


def _add_candidate(
    env: _Env,
    *,
    ext: str = ".png",
    blur: float = 0.0,
    mime_type: str | None = "image/png",
    text: str = CLEAR_TEXT,
    with_binary: bool = True,
) -> ParseCandidate:
    # 每个候选的像素内容必须唯一：否则同内容候选会算出同一个 sha256，
    # 同 sha 的条目会互相覆盖（例如 limit 用例无法区分「第一条解析 / 第二条限流」）。
    # 注意唯一标记必须**落在画布内**：常量文本实测宽 ~1064px > 画布 900px，若把 uuid
    # 追加在同一行末尾会被整段裁掉（实测 400 对里有 8% 像素完全相同 → sha256 撞车 →
    # 第二条被幂等判成 ALREADY_SUCCESS，用例随机假红）。故换行后单独绘制，起点 x=30 必然可见。
    source = _make_text_image(
        env.workspace / "sources" / f"{uuid.uuid4().hex}{ext}",
        blur=blur,
        text=f"{text}\n{uuid.uuid4().hex}",
    )
    blob = source.read_bytes()
    sha256 = hashlib.sha256(blob).hexdigest()
    original = build_vault_artifact_paths(build_vault_dir(env.raw_vault_root, sha256))["original_bin"]
    if with_binary:
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(blob)
    return ParseCandidate(
        content_uid=f"uid-{sha256[:12]}",
        sha256=sha256,
        file_ext=ext,
        mime_type=mime_type,
        master_file_instance_uid=None,
        original_path=original,
    )


@dataclass
class FakeEngine:
    """假引擎：可计数、可注入错误 / 延迟 / 置信度 / mock 标记。"""

    text: str = "OCR 识别结果\n第二行\n"
    confidence_mean: float | None = 0.91
    confidence_min: float | None = 0.62
    sample_count: int = 47
    engine_name: str = "paddleocr"
    mock: bool = False
    is_mock: bool = False
    delay: float = 0.0
    error: BaseException | None = None
    calls: list[tuple[Path, int]] = field(default_factory=list)

    def recognize(self, image_path: Path, *, timeout_seconds: int) -> OcrResult:
        self.calls.append((Path(image_path), timeout_seconds))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return OcrResult(
            text=self.text,
            confidence_mean=self.confidence_mean,
            confidence_min=self.confidence_min,
            sample_count=self.sample_count,
            engine=self.engine_name,
            mock=self.mock,
            engine_meta={},
        )


def _real_vlm(**kwargs: Any) -> FakeEngine:
    kwargs.setdefault("engine_name", "vlm")
    return FakeEngine(**kwargs)


def _service(
    env: _Env,
    candidates: list[ParseCandidate],
    *,
    ocr: FakeEngine | None = None,
    vlm: Any | None = None,
    paddleocr_available: bool = False,
    scorer: Any | None = None,
    preprocessor: Any | None = None,
    image_config: ImageParseConfig | None = None,
) -> ImageParserService:
    return ImageParserService(
        env.config,
        image_config=image_config or load_image_parse_config(),
        ocr_client=ocr if ocr is not None else FakeEngine(),
        vlm_client=vlm if vlm is not None else MockVlmClient(),
        scorer=scorer,
        preprocessor=preprocessor,
        candidates_loader=lambda limit: list(candidates),
        paddleocr_available=paddleocr_available,
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _item(result: Any, sha256: str) -> Any:
    for item in result.items:
        if item.sha256 == sha256:
            return item
    raise AssertionError(f"item not found for {sha256}")


# ======================================================================================
# A. 图像质量评分（adapters/image_quality.py）
# ======================================================================================
def test_clear_image_scores_above_frozen_threshold(tmp_path: Path) -> None:
    result = score_image(_make_text_image(tmp_path / "clear.png"))

    assert result.method == "laplacian"
    assert result.score is not None
    assert result.score >= 100.0  # plan §2.3 冻结阈值


def test_blurred_image_scores_below_frozen_threshold(tmp_path: Path) -> None:
    result = score_image(_make_text_image(tmp_path / "blur.png", blur=BLUR_RADIUS))

    assert result.method == "laplacian"
    assert result.score is not None
    assert result.score < 100.0


def test_scorer_auto_uses_laplacian_when_cv2_available(tmp_path: Path) -> None:
    assert image_quality.import_cv2() is not None, "本用例要求环境具备 cv2"
    assert score_image(_make_text_image(tmp_path / "a.png"), "auto").method == "laplacian"


def test_scorer_falls_back_to_pillow_when_cv2_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image_quality, "import_cv2", lambda: None)

    result = score_image(_make_text_image(tmp_path / "p.png"))

    assert result.method == "pillow_gradient"
    assert result.score is not None


def test_scorer_unknown_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        score_image(_make_text_image(tmp_path / "u.png"), "no-such-scorer")


def test_scorer_returns_unavailable_for_unreadable_image(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image at all")

    result = score_image(broken)

    assert result.score is None
    assert result.method == "unavailable"


def test_scorer_missing_file_returns_unavailable(tmp_path: Path) -> None:
    result = score_image(tmp_path / "nope.png")

    assert result.score is None
    assert result.method == "unavailable"


# ======================================================================================
# B. 预处理（adapters/image_preprocess.py）—— 失败降级不崩
# ======================================================================================
def test_preprocess_applies_configured_ops(tmp_path: Path) -> None:
    source = _make_text_image(tmp_path / "src.png")
    staging = tmp_path / "staging"

    output = preprocess_image(source, staging, ["deskew", "denoise", "binarize"])

    assert output.output_path.is_file()
    assert output.applied_ops == ("deskew", "denoise", "binarize")
    assert output.skipped_ops == ()
    for name in output.applied_ops:
        assert (staging / "preprocess" / f"{name}.png").is_file()


def test_preprocess_degrades_when_every_op_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_text_image(tmp_path / "src.png")

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("op exploded")

    monkeypatch.setattr(image_preprocess, "_apply_op", _boom)

    output = preprocess_image(source, tmp_path / "staging", ["deskew", "denoise"])

    assert output.applied_ops == ()
    assert set(output.skipped_ops) == {"deskew:error", "denoise:error"}
    assert output.output_path.is_file()  # 回退到原图，管线继续
    assert output.warnings


def test_preprocess_without_cv2_skips_all_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 cv2 环境必须全绿：全部 op 标 skipped 且不抛。"""
    source = _make_text_image(tmp_path / "src.png")
    monkeypatch.setattr(image_preprocess, "import_cv2", lambda: None)

    output = preprocess_image(source, tmp_path / "staging", ["deskew", "binarize"])

    assert output.applied_ops == ()
    assert output.skipped_ops == ("deskew:opencv_missing", "binarize:opencv_missing")
    assert output.output_path.is_file()


def test_preprocess_unreadable_source_is_marked_skipped(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"definitely not an image")

    output = preprocess_image(broken, tmp_path / "staging", ["deskew"])

    assert output.applied_ops == ()
    assert output.skipped_ops == ("deskew:unreadable_source",)


def test_preprocess_empty_ops_is_passthrough(tmp_path: Path) -> None:
    source = _make_text_image(tmp_path / "src.png")

    output = preprocess_image(source, tmp_path / "staging", [])

    assert output.applied_ops == ()
    assert output.skipped_ops == ()
    assert output.output_path.is_file()


# ======================================================================================
# C. 引擎适配器（vlm_adapter / ocr_paddle）
# ======================================================================================
def test_mock_vlm_client_flags_mock_and_yields_no_text() -> None:
    client = MockVlmClient()

    result = client.recognize(Path("whatever.png"), timeout_seconds=5)

    assert result.mock is True
    assert result.text == ""
    assert result.engine == "vlm"
    assert "mock" in str(result.engine_meta.get("note", "")).lower()
    assert client.is_mock is True


def test_mock_vlm_client_satisfies_protocol() -> None:
    assert isinstance(MockVlmClient(), VlmClient)


def test_build_vlm_client_returns_mock_for_mock_adapter() -> None:
    assert isinstance(build_vlm_client({"adapter": "mock"}), MockVlmClient)


def test_build_vlm_client_rejects_unknown_adapter() -> None:
    with pytest.raises(ValueError):
        build_vlm_client({"adapter": "openai-http"})


def test_check_paddleocr_available_reports_dependency_missing() -> None:
    if ocr_paddle.import_paddleocr() is not None:  # pragma: no cover - 本机未装
        pytest.skip("paddleocr installed; dependency-missing branch unreachable")

    with pytest.raises(EngineDependencyMissing) as excinfo:
        check_paddleocr_available()

    assert "paddleocr" in str(excinfo.value).lower()
    assert "DEPENDENCY_MISSING" in str(excinfo.value)


def test_paddleocr_client_recognize_without_dependency_raises() -> None:
    if ocr_paddle.import_paddleocr() is not None:  # pragma: no cover
        pytest.skip("paddleocr installed")
    from app.adapters.ocr_paddle import PaddleOcrClient

    with pytest.raises(EngineDependencyMissing):
        PaddleOcrClient().recognize(Path("x.png"), timeout_seconds=5)


# ======================================================================================
# D. 引擎路由真值表（plan §6）
# ======================================================================================
def test_auto_clear_paddleocr_available_uses_paddleocr(env: _Env) -> None:
    candidate = _add_candidate(env)
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_AUTO), candidate.sha256)

    assert item.decision == DECISION_PARSED
    assert item.status == STATUS_SUCCESS
    assert item.engine_actual == "paddleocr"
    assert item.parser_profile == PARSER_PROFILE_PADDLEOCR
    assert len(ocr.calls) == 1


def test_auto_low_quality_with_real_vlm_routes_to_vlm(env: _Env) -> None:
    candidate = _add_candidate(env, blur=BLUR_RADIUS)
    ocr, vlm = FakeEngine(), _real_vlm()
    service = _service(env, [candidate], ocr=ocr, vlm=vlm, paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_AUTO), candidate.sha256)

    assert item.decision == DECISION_PARSED
    assert item.engine_actual == "vlm"
    assert item.parser_profile == PARSER_PROFILE_VLM
    assert ocr.calls == []
    assert len(vlm.calls) == 1


def test_auto_low_quality_with_mock_vlm_is_unsupported_no_writes(env: _Env) -> None:
    candidate = _add_candidate(env, blur=BLUR_RADIUS)
    service = _service(env, [candidate], vlm=MockVlmClient(), paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_AUTO), candidate.sha256)

    assert item.decision == DECISION_UNSUPPORTED_WITH_HINT
    assert item.status == STATUS_SKIPPED
    assert not build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_dir.exists()


def test_auto_clear_paddleocr_missing_real_vlm_fallback(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], vlm=_real_vlm(), paddleocr_available=False)

    item = _item(service.parse_many(engine=ENGINE_AUTO), candidate.sha256)

    assert item.decision == DECISION_PARSED
    assert item.engine_actual == "vlm"
    metadata = _read_json(
        build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_metadata
    )
    assert metadata["engine"]["fallback_reason"] == "paddleocr_missing"
    assert metadata["parser_profile"] == PARSER_PROFILE_VLM


def test_auto_clear_paddleocr_missing_mock_vlm_unsupported(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], vlm=MockVlmClient(), paddleocr_available=False)

    item = _item(service.parse_many(engine=ENGINE_AUTO), candidate.sha256)

    assert item.decision == DECISION_UNSUPPORTED_WITH_HINT
    assert item.status == STATUS_SKIPPED


def test_engine_paddleocr_missing_is_dependency_missing(env: _Env) -> None:
    candidate = _add_candidate(env)
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=False)

    result = service.parse_many(engine=ENGINE_PADDLEOCR)
    item = _item(result, candidate.sha256)

    assert item.decision == DECISION_DEPENDENCY_MISSING
    assert item.status == STATUS_FAILED
    assert ocr.calls == []
    assert any(error.code == "DEPENDENCY_MISSING" for error in result.errors)
    assert not build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_dir.exists()


def test_engine_paddleocr_low_quality_warns_but_parses(env: _Env) -> None:
    candidate = _add_candidate(env, blur=BLUR_RADIUS)
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)

    assert item.decision == DECISION_PARSED
    assert item.engine_actual == "paddleocr"
    assert item.warnings
    assert len(ocr.calls) == 1


def test_engine_vlm_with_mock_is_unsupported(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], vlm=MockVlmClient(), paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_VLM), candidate.sha256)

    assert item.decision == DECISION_UNSUPPORTED_WITH_HINT
    assert item.status == STATUS_SKIPPED


def test_engine_vlm_with_real_client_parses(env: _Env) -> None:
    candidate = _add_candidate(env)
    vlm = _real_vlm()
    service = _service(env, [candidate], vlm=vlm, paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_VLM), candidate.sha256)

    assert item.decision == DECISION_PARSED
    assert item.parser_profile == PARSER_PROFILE_VLM
    assert len(vlm.calls) == 1


def test_score_none_failsafe_routes_to_vlm(env: _Env) -> None:
    """D-3：评分失败（score=None）fail-safe 到 VLM 路径。"""
    candidate = _add_candidate(env)
    vlm = _real_vlm()
    service = _service(
        env,
        [candidate],
        vlm=vlm,
        paddleocr_available=True,
        scorer=lambda path, mode="auto": ImageQualityResult(None, "unavailable", "boom"),
    )

    item = _item(service.parse_many(engine=ENGINE_AUTO), candidate.sha256)

    assert item.decision == DECISION_PARSED
    assert item.engine_actual == "vlm"


def test_score_none_with_mock_vlm_is_unsupported(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(
        env,
        [candidate],
        vlm=MockVlmClient(),
        paddleocr_available=True,
        scorer=lambda path, mode="auto": ImageQualityResult(None, "unavailable", "boom"),
    )

    item = _item(service.parse_many(engine=ENGINE_AUTO), candidate.sha256)

    assert item.decision == DECISION_UNSUPPORTED_WITH_HINT
    assert item.status == STATUS_SKIPPED


# ======================================================================================
# E. D-2 诚实性红线（A015-06 变异锚点）
# ======================================================================================
def test_mock_result_is_never_persisted_with_forged_text(env: _Env) -> None:
    """mock 引擎即便返回伪造文本，也必须零落盘（A015-06 变异锚点）。

    变异体（删掉 `result.mock` 拒绝分支）→ 本条必 FAIL。
    """
    candidate = _add_candidate(env)
    forged = _real_vlm(text="伪造的识别结果，不得落盘", mock=True, is_mock=True)
    service = _service(env, [candidate], vlm=forged, paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_VLM), candidate.sha256)

    assert forged.calls, "引擎确实被调用过，因此断言的是「拒绝落盘」而非「未调用」"
    assert item.decision == DECISION_UNSUPPORTED_WITH_HINT
    assert item.status == STATUS_SKIPPED
    paths = build_image_parsed_paths(env.parsed_root, candidate.sha256)
    assert not paths.parsed_dir.exists()
    assert not paths.parsed_text.exists()
    assert not paths.parsed_metadata.exists()
    assert "伪造" not in str(item.to_dict())


def test_mock_paddleocr_result_is_also_rejected(env: _Env) -> None:
    """同一红线对 paddleocr 侧同样生效（防只挡 VLM 的假绿）。"""
    candidate = _add_candidate(env)
    ocr = FakeEngine(engine_name="paddleocr", mock=True, is_mock=True, text="伪造")
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)

    assert item.decision == DECISION_UNSUPPORTED_WITH_HINT
    assert not build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_dir.exists()


# ======================================================================================
# F. 产物契约（spec §6 / §3.1 / D-5）
# ======================================================================================
def test_build_image_parsed_paths_follows_by_hash_layout(env: _Env) -> None:
    sha = "ab" * 32
    paths = build_image_parsed_paths(env.parsed_root, sha)

    assert paths.parsed_dir == env.parsed_root / "by_hash" / "ab" / "ab" / sha
    assert paths.parsed_text.name == "parsed_text.md"
    assert paths.parsed_metadata.name == "parsed_metadata.json"
    assert paths.parse_manifest.name == "parse_manifest.json"
    assert paths.assets_dir == paths.parsed_dir / "assets" / "preprocess"


def test_success_run_writes_spec_metadata_contract(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], ocr=FakeEngine(), paddleocr_available=True)

    _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)
    metadata = _read_json(
        build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_metadata
    )

    assert metadata["parser_name"] == PARSER_NAME == "image_ocr"
    assert metadata["parser_profile"] == PARSER_PROFILE_PADDLEOCR
    assert metadata["parser_adapter_version"] == PARSER_ADAPTER_VERSION == "015_mvp_v1"
    assert metadata["route_type"] == "IMAGE"
    assert metadata["image_quality"]["method"] == "laplacian"
    assert metadata["image_quality"]["score"] > 0
    assert metadata["ocr_confidence"] == {"mean": 0.91, "min": 0.62, "sample_count": 47}
    assert metadata["engine"] == {
        "requested": "paddleocr",
        "actual": "paddleocr",
        "fallback_reason": None,
    }


def test_preprocess_ops_recorded_and_skipped_ops_not_claimed(env: _Env) -> None:
    """A015-04：extra.preprocess_ops 反映实际执行结果，失败 op 不得静默声称已执行。"""
    candidate = _add_candidate(env)
    service = _service(env, [candidate], ocr=FakeEngine(), paddleocr_available=True)
    _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)

    metadata = _read_json(
        build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_metadata
    )

    extra = metadata["extra"]
    assert isinstance(extra["preprocess_ops"], list)
    for op in extra["preprocess_ops"]:
        assert op in {"deskew", "denoise", "binarize"}
    # 未执行的 op 只能出现在 skipped 明细里，且带原因
    for op in extra["preprocess_skipped_ops"]:
        assert ":" in op
    assert metadata["image_quality"]["preprocess_ops"] == extra["preprocess_ops"]


def test_assets_copied_and_indexed_in_metadata(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], ocr=FakeEngine(), paddleocr_available=True)
    _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)

    paths = build_image_parsed_paths(env.parsed_root, candidate.sha256)
    metadata = _read_json(paths.parsed_metadata)
    manifest = _read_json(paths.parse_manifest)

    assert metadata["extra"]["asset_files"], "op 已执行时必须有资产索引"
    for entry in metadata["extra"]["asset_files"]:
        assert (paths.parsed_dir / entry["relative_path"]).is_file()
        assert len(entry["sha256"]) == 64
    assert manifest["assets_dir"] == paths.assets_dir.as_posix()


def test_ocr_confidence_is_null_when_engine_reports_none(env: _Env) -> None:
    """spec §10：无真实置信度时不得编造。"""
    candidate = _add_candidate(env)
    ocr = FakeEngine(confidence_mean=None, confidence_min=None, sample_count=0)
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)
    _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)

    metadata = _read_json(
        build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_metadata
    )

    assert metadata["ocr_confidence"]["mean"] is None
    assert metadata["ocr_confidence"]["min"] is None


def test_empty_engine_text_maps_to_empty_status(env: _Env) -> None:
    candidate = _add_candidate(env)
    ocr = FakeEngine(text="   \n")
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    item = _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)

    assert item.status == STATUS_EMPTY
    assert item.decision == DECISION_PARSED


# ======================================================================================
# G. 幂等 / force / dry-run
# ======================================================================================
def test_second_identical_run_is_already_success(env: _Env) -> None:
    candidate = _add_candidate(env)
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    first = _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)
    second = _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)

    assert first.decision == DECISION_PARSED
    assert second.decision == DECISION_ALREADY_SUCCESS
    assert second.skip_reason == "idempotent_success_manifest"
    assert len(ocr.calls) == 1, "幂等命中时不得再调用引擎"


def test_profile_change_triggers_reparse(env: _Env) -> None:
    """TC015-13：image_paddleocr_v1 → image_vlm_v1 应重跑。"""
    candidate = _add_candidate(env)
    ocr, vlm = FakeEngine(), _real_vlm()
    service = _service(env, [candidate], ocr=ocr, vlm=vlm, paddleocr_available=True)

    _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)
    second = _item(service.parse_many(engine=ENGINE_VLM), candidate.sha256)

    assert second.decision == DECISION_PARSED
    assert len(vlm.calls) == 1


def test_force_reparses_and_clears_previous_outputs(env: _Env) -> None:
    candidate = _add_candidate(env)
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    _item(service.parse_many(engine=ENGINE_PADDLEOCR), candidate.sha256)
    stale = build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_dir / "stale.txt"
    stale.write_text("stale", encoding="utf-8")

    item = _item(service.parse_many(engine=ENGINE_PADDLEOCR, force=True), candidate.sha256)

    assert item.decision == DECISION_PARSED
    assert len(ocr.calls) == 2
    assert not stale.exists()


def test_dry_run_calls_no_engine_and_writes_nothing(env: _Env) -> None:
    candidate = _add_candidate(env)
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    result = service.parse_many(engine=ENGINE_PADDLEOCR, dry_run=True)
    item = _item(result, candidate.sha256)

    assert result.dry_run is True
    assert item.dry_run_action == "would_parse"
    assert ocr.calls == []
    assert not build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_dir.exists()
    assert result.report_path is None
    assert not env.reports_root.exists() or not list(env.reports_root.glob("*.json"))


# ======================================================================================
# H. 批处理隔离 / 错误分类 / 报告
# ======================================================================================
def test_engine_error_is_recorded_and_batch_continues(env: _Env) -> None:
    bad = _add_candidate(env)
    good = _add_candidate(env)
    ocr = FakeEngine(error=RuntimeError("engine exploded"))
    service = _service(env, [bad, good], ocr=ocr, paddleocr_available=True)

    # 逐个驱动，确保「失败不中断整批」
    first = service.parse_many(sha256=bad.sha256, engine=ENGINE_PADDLEOCR)
    bad_item = _item(first, bad.sha256)
    assert bad_item.decision == DECISION_ENGINE_ERROR
    assert bad_item.status == STATUS_FAILED
    assert any(error.code == "ENGINE_ERROR" for error in first.errors)

    ocr.error = None
    second = service.parse_many(sha256=good.sha256, engine=ENGINE_PADDLEOCR)
    assert _item(second, good.sha256).decision == DECISION_PARSED


def test_engine_timeout_is_recorded_as_failed(env: _Env) -> None:
    candidate = _add_candidate(env)
    slow = FakeEngine(delay=1.2)
    service = _service(env, [candidate], ocr=slow, paddleocr_available=True)

    result = service.parse_many(engine=ENGINE_PADDLEOCR, timeout_seconds=1)
    item = _item(result, candidate.sha256)

    assert item.decision == DECISION_TIMEOUT
    assert item.status == STATUS_FAILED
    assert any(error.code == "TIMEOUT" for error in result.errors)
    assert not build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_dir.exists()


def test_non_image_candidate_is_route_mismatch(env: _Env) -> None:
    candidate = _add_candidate(env, ext=".txt", mime_type="text/plain")
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    result = service.parse_many(engine=ENGINE_PADDLEOCR)
    item = _item(result, candidate.sha256)

    assert item.decision == DECISION_ROUTE_MISMATCH
    assert item.status == STATUS_SKIPPED
    assert ocr.calls == []
    assert result.in_scope_candidates == 0
    assert not build_image_parsed_paths(env.parsed_root, candidate.sha256).parsed_dir.exists()


def test_missing_original_bin_is_failed_with_reason(env: _Env) -> None:
    candidate = _add_candidate(env, with_binary=False)
    ocr = FakeEngine()
    service = _service(env, [candidate], ocr=ocr, paddleocr_available=True)

    result = service.parse_many(engine=ENGINE_PADDLEOCR)
    item = _item(result, candidate.sha256)

    assert item.status == STATUS_FAILED
    assert item.skip_reason == "missing_original_bin"
    assert ocr.calls == []
    assert any(error.code == "MISSING_ORIGINAL_BIN" for error in result.errors)


def test_limit_marks_remaining_candidates_as_parse_limit_reached(env: _Env) -> None:
    first, second = _add_candidate(env), _add_candidate(env)
    ocr = FakeEngine()
    service = _service(env, [first, second], ocr=ocr, paddleocr_available=True)

    result = service.parse_many(engine=ENGINE_PADDLEOCR, limit=1)

    actions = {item.sha256: item for item in result.items}
    assert actions[first.sha256].decision == DECISION_PARSED
    assert actions[second.sha256].skip_reason == "parse_limit_reached"
    assert actions[second.sha256].status == STATUS_SKIPPED
    assert len(ocr.calls) == 1


def test_report_payload_contract(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], ocr=FakeEngine(), paddleocr_available=True)

    result = service.parse_many(engine=ENGINE_PADDLEOCR)

    assert result.report_path is not None
    report = _read_json(result.report_path)
    assert report["report_type"] == "parse_image_report"
    assert report["parser_name"] == PARSER_NAME
    assert set(report["summary"]) == {
        "total",
        "in_scope",
        "parsed",
        "skipped",
        "failed",
        "unsupported",
    }
    assert report["summary"]["parsed"] == 1
    assert report["filters"]["engine"] == "paddleocr"
    assert result.report_path.name.startswith("parse_image_report_")


def test_register_flag_forwards_report_to_registry(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], ocr=FakeEngine(), paddleocr_available=True)

    class _FakeRegistry:
        def __init__(self) -> None:
            self.paths: list[Path] = []

        def register_parse_report(self, *, report_path: Path, dry_run: bool = False) -> Any:
            self.paths.append(report_path)
            return None

    registry = _FakeRegistry()
    result = service.parse_many(engine=ENGINE_PADDLEOCR, register=True, registry_service=registry)

    assert registry.paths == [result.report_path]


def test_dry_run_never_registers(env: _Env) -> None:
    candidate = _add_candidate(env)
    service = _service(env, [candidate], ocr=FakeEngine(), paddleocr_available=True)

    class _FakeRegistry:
        def register_parse_report(self, **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError("dry-run 不得触发注册")

    result = service.parse_many(engine=ENGINE_PADDLEOCR, dry_run=True, register=True)

    assert result.report_path is None


# ======================================================================================
# I. CLI（app/cli/main.py，append-only）
# ======================================================================================
def test_cli_exposes_parse_images_command() -> None:
    names = {getattr(command, "name", None) for command in app.registered_commands}

    assert "parse-images" in names


def test_cli_paddleocr_missing_exits_one(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A015-03：`--engine paddleocr` 且依赖缺失 → 非 dry-run 退出码 1，不静默降级。"""
    import app.cli.main as cli_main

    def _boom() -> None:
        raise EngineDependencyMissing("DEPENDENCY_MISSING: paddleocr 未安装")

    monkeypatch.setattr(cli_main, "check_paddleocr_available", _boom)

    result = CliRunner().invoke(
        app,
        [
            "parse-images",
            "--config",
            str(env.config_path),
            "--engine",
            "paddleocr",
        ],
    )

    assert result.exit_code == 1
    assert "DEPENDENCY_MISSING" in result.output


def test_cli_dry_run_flag_is_forwarded_to_service(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dry-run 选项必须落到 service（避免 CLI 层「看起来 dry-run」但实际落盘）。"""
    import app.cli.main as cli_main

    captured: dict[str, Any] = {}

    @dataclass
    class _StubResult:
        total_candidates: int = 0
        in_scope_candidates: int = 0
        parsed_count: int = 0
        skipped_count: int = 0
        failed_count: int = 0
        unsupported_count: int = 0
        empty_count: int = 0
        dry_run: bool = True
        items: list[Any] = field(default_factory=list)
        errors: list[Any] = field(default_factory=list)
        report_path: Path | None = None
        plans: list[Any] = field(default_factory=list)

    class _StubService:
        def __init__(self, config: Any, **kwargs: Any) -> None:
            captured["init_kwargs"] = kwargs

        def parse_many(self, **kwargs: Any) -> _StubResult:
            captured["parse_kwargs"] = kwargs
            return _StubResult()

    monkeypatch.setattr(cli_main, "ImageParserService", _StubService)

    result = CliRunner().invoke(
        app,
        [
            "parse-images",
            "--config",
            str(env.config_path),
            "--dry-run",
            "--engine",
            "auto",
            "--limit",
            "5",
            "--no-register",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["parse_kwargs"]["dry_run"] is True
    assert captured["parse_kwargs"]["limit"] == 5
    assert captured["parse_kwargs"]["register"] is False


def test_cli_sha256_and_engine_options_are_forwarded(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.cli.main as cli_main

    captured: dict[str, Any] = {}

    @dataclass
    class _StubResult:
        total_candidates: int = 0
        in_scope_candidates: int = 0
        parsed_count: int = 0
        skipped_count: int = 0
        failed_count: int = 0
        unsupported_count: int = 0
        empty_count: int = 0
        dry_run: bool = False
        items: list[Any] = field(default_factory=list)
        errors: list[Any] = field(default_factory=list)
        report_path: Path | None = None
        plans: list[Any] = field(default_factory=list)

    class _StubService:
        def __init__(self, config: Any, **kwargs: Any) -> None:
            pass

        def parse_many(self, **kwargs: Any) -> _StubResult:
            captured.update(kwargs)
            return _StubResult()

    monkeypatch.setattr(cli_main, "ImageParserService", _StubService)

    sha = "c" * 64
    result = CliRunner().invoke(
        app,
        [
            "parse-images",
            "--config",
            str(env.config_path),
            "--sha256",
            sha,
            "--engine",
            "vlm",
            "--timeout",
            "42",
            "--register",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["sha256"] == sha
    assert captured["engine"] == "vlm"
    assert captured["timeout_seconds"] == 42
    assert captured["register"] is True


def test_cli_014_options_still_present_in_source() -> None:
    """T009 回归锚点：014 的三个字面量计数不得下降（源码级，非 --help 调用）。

    路径锚定到 `__file__` 对应的 backend 根，**不依赖 CWD**：既支持项目约定
    `cd backend && pytest`，也支持仓库根 `pytest backend/tests/...`。
    """
    source = (
        Path(__file__).resolve().parents[1] / "app" / "cli" / "main.py"
    ).read_text(encoding="utf-8")

    assert source.count("routing-rules-version") >= 2
    assert source.count("no-sniff-cache") >= 2
    assert source.count("dry-run") >= 9


# ======================================================================================
# J. DB 候选加载（无 MySQL 时 skip，判定不变）
# ======================================================================================
def _mysql_available(env: _Env) -> bool:
    from sqlalchemy import create_engine

    from app.core.database import create_db_engine

    try:
        engine = create_db_engine(env.config)
        with engine.connect():
            return True
    except Exception:
        return False


def test_db_candidate_loader_returns_image_candidates(env: _Env) -> None:
    if not _mysql_available(env):
        pytest.skip("MySQL unavailable for 015 DB candidate-loader test")

    service = ImageParserService(env.config, image_config=load_image_parse_config())
    candidates = service._load_candidates(sha256=None, content_uid=None)

    assert isinstance(candidates, list)
    for candidate in candidates:
        assert len(candidate.sha256) == 64
        assert candidate.original_path is not None
