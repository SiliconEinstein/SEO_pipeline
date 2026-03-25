"""Lance on TOS storage for optimization history tracking."""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import lance
import pyarrow as pa

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES_SCHEMA = pa.schema([
    pa.field("template_hash", pa.string()),
    pa.field("template_content", pa.string()),
    pa.field("created_at", pa.timestamp("us")),
])

OPTIMIZATION_HISTORY_SCHEMA = pa.schema([
    pa.field("path", pa.string()),
    pa.field("optimized_at", pa.timestamp("us")),
    pa.field("template_hash", pa.string()),
    pa.field("context_json", pa.string()),
    pa.field("original_title", pa.string()),
    pa.field("optimized_title", pa.string()),
    pa.field("original_description", pa.string()),
    pa.field("optimized_description", pa.string()),
    pa.field("audit_issues", pa.string()),
    pa.field("priority_score", pa.float64()),
    pa.field("subtype", pa.string()),
    pa.field("model", pa.string()),
])


class LanceStore:
    """Lance on TOS read/write wrapper."""

    def __init__(self, config: dict):
        tos = config.get("tos", {})
        self._bucket = tos.get("bucket", "datainfra-test")
        self._endpoint = tos.get("endpoint", "tos-s3-cn-beijing.volces.com")
        self._base_path = tos.get("base_path", "science_pedia/SEO").strip("/")

        ak = os.environ.get("TOS_ACCESS_KEY", tos.get("access_key", ""))
        sk = os.environ.get("TOS_SECRET_KEY", tos.get("secret_key", ""))
        if not ak or not sk:
            raise RuntimeError(
                "TOS 凭证缺失: 请设置环境变量 TOS_ACCESS_KEY / TOS_SECRET_KEY"
            )

        self._storage_options = {
            "access_key_id": ak,
            "secret_access_key": sk,
            "endpoint": f"https://{self._bucket}.{self._endpoint}",
            "virtual_hosted_style_request": "true",
        }

    # ------------------------------------------------------------------
    # URI helpers
    # ------------------------------------------------------------------

    def _table_uri(self, table_name: str) -> str:
        return f"s3://{self._bucket}/{self._base_path}/{table_name}.lance"

    # ------------------------------------------------------------------
    # Low-level read/write
    # ------------------------------------------------------------------

    def _dataset(self, table_name: str):
        """Open an existing Lance dataset. Returns None if table doesn't exist."""
        try:
            return lance.dataset(
                self._table_uri(table_name),
                storage_options=self._storage_options,
            )
        except Exception as e:
            logger.debug("Lance 表 %s 未打开: %s", table_name, e)
            return None

    def _write(self, table_name: str, data: list[dict], schema: pa.Schema):
        """Append rows. Table must already exist (use create_tables() first)."""
        uri = self._table_uri(table_name)
        table = pa.Table.from_pylist(data, schema=schema)

        ds = self._dataset(table_name)
        if ds is None:
            raise RuntimeError(
                f"Lance 表 {table_name} 不存在，请先运行: "
                f"uv run python main.py init-lance"
            )
        lance.write_dataset(
            table, uri,
            storage_options=self._storage_options,
            mode="append",
        )

    # ------------------------------------------------------------------
    # Table management
    # ------------------------------------------------------------------

    def create_tables(self):
        """Create empty Lance tables if they don't already exist."""
        tables = {
            "prompt_templates": PROMPT_TEMPLATES_SCHEMA,
            "optimization_history": OPTIMIZATION_HISTORY_SCHEMA,
        }
        for name, schema in tables.items():
            ds = self._dataset(name)
            if ds is not None:
                logger.info("Lance 表已存在，跳过: %s", name)
                continue
            uri = self._table_uri(name)
            empty = pa.Table.from_pylist([], schema=schema)
            lance.write_dataset(
                empty, uri,
                storage_options=self._storage_options,
                mode="create",
            )
            logger.info("Lance 表已创建: %s", uri)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_recently_optimized(self, days: int = 30) -> set[str]:
        """Return paths optimized within the last *days* days."""
        ds = self._dataset("optimization_history")
        if ds is None:
            return set()

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            rows = ds.to_table(
                filter=f"optimized_at >= timestamp '{cutoff_str}'",
                columns=["path"],
            ).to_pylist()
            return {r["path"] for r in rows}
        except Exception as e:
            logger.warning("Lance 查询失败: %s", e)
            return set()

    def save_prompt_template(self, content: str) -> str:
        """Store template (deduplicated by SHA-256 hash). Return hash."""
        template_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Check if already stored
        ds = self._dataset("prompt_templates")
        if ds is not None:
            try:
                existing = ds.to_table(
                    filter=f"template_hash = '{template_hash}'",
                    columns=["template_hash"],
                ).to_pylist()
                if existing:
                    return template_hash
            except Exception:
                pass  # table exists but query failed, write anyway

        self._write("prompt_templates", [{
            "template_hash": template_hash,
            "template_content": content,
            "created_at": datetime.now(timezone.utc),
        }], PROMPT_TEMPLATES_SCHEMA)
        logger.info("模板已存储: %s", template_hash[:12])
        return template_hash

    def record_optimizations(self, records: list[dict]):
        """Batch-append optimization history records."""
        if not records:
            return
        self._write("optimization_history", records, OPTIMIZATION_HISTORY_SCHEMA)
        logger.info("已写入 %d 条优化记录", len(records))
