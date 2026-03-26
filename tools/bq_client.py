import logging
from typing import Optional, List, Dict, Any

import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger(__name__)

DATASET_ID = "bigquery-public-data.thelook_ecommerce"
AVAILABLE_TABLES = ["orders", "order_items", "products", "users"]

TABLE_SCHEMA_CONTEXT = """
Dataset: bigquery-public-data.thelook_ecommerce

Table: orders
  - order_id (INTEGER) — unique order identifier
  - user_id (INTEGER) — FK to users.id
  - status (STRING) — Shipped, Complete, Processing, Cancelled, Returned
  - gender (STRING) — M/F
  - created_at (TIMESTAMP)
  - returned_at (TIMESTAMP, nullable)
  - shipped_at (TIMESTAMP, nullable)
  - delivered_at (TIMESTAMP, nullable)
  - num_of_item (INTEGER)

Table: order_items
  - id (INTEGER) — line item id
  - order_id (INTEGER) — FK to orders.order_id
  - user_id (INTEGER) — FK to users.id
  - product_id (INTEGER) — FK to products.id
  - inventory_item_id (INTEGER)
  - status (STRING)
  - created_at (TIMESTAMP)
  - shipped_at (TIMESTAMP, nullable)
  - delivered_at (TIMESTAMP, nullable)
  - returned_at (TIMESTAMP, nullable)
  - sale_price (FLOAT) — actual sale price (use this for revenue)

Table: products
  - id (INTEGER) — unique product id
  - cost (FLOAT) — product cost
  - category (STRING) — product category
  - name (STRING) — product name
  - brand (STRING) — brand name
  - retail_price (FLOAT) — listed retail price
  - department (STRING) — Men/Women
  - sku (STRING)
  - distribution_center_id (INTEGER)

Table: users
  - id (INTEGER) — unique user id
  - first_name (STRING)
  - last_name (STRING)
  - email (STRING) — ⚠️ PII - never expose
  - age (INTEGER)
  - gender (STRING) — M/F
  - state (STRING)
  - street_address (STRING) — ⚠️ PII - never expose
  - postal_code (STRING)
  - city (STRING)
  - country (STRING)
  - latitude (FLOAT)
  - longitude (FLOAT)
  - traffic_source (STRING)
  - created_at (TIMESTAMP)

Key relationships:
  orders.user_id → users.id
  order_items.order_id → orders.order_id
  order_items.product_id → products.id

Rules:
  - Always use fully qualified table names: `bigquery-public-data.thelook_ecommerce.<table>`
  - Use sale_price (not retail_price) for revenue calculations
  - Exclude Cancelled/Returned orders unless specifically asked
  - NEVER select email, street_address, postal_code, or latitude/longitude from users
"""


class BigQueryRunner:
    """BigQuery client for executing SQL queries and returning DataFrame results."""

    def __init__(self, project_id: Optional[str] = None) -> None:
        self.dataset_id = DATASET_ID
        try:
            self.client = bigquery.Client(project=project_id)
            logger.info("BigQuery client initialized for dataset: %s", self.dataset_id)
        except Exception as e:
            logger.error("Failed to initialize BigQuery client: %s", e)
            raise

    def execute_query(self, sql_query: str) -> pd.DataFrame:
        """Execute a SQL query and return results as a DataFrame."""
        try:
            logger.info("Executing BigQuery query")
            query_job = self.client.query(sql_query)
            df = query_job.result().to_dataframe()
            logger.info("Query returned %d rows", len(df))
            return df
        except Exception as e:
            logger.error("BigQuery execution failed: %s", e)
            raise

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """Get schema information for a specific table."""
        try:
            table_ref = f"{self.dataset_id}.{table_name}"
            table = self.client.get_table(table_ref)
            schema_info = []
            for field in table.schema:
                schema_info.append(
                    {
                        "name": field.name,
                        "type": field.field_type,
                        "mode": field.mode,
                        "description": field.description or "",
                    }
                )
            logger.info("Retrieved schema for table %s", table_name)
            return schema_info
        except Exception as e:
            logger.error("Failed to get schema for %s: %s", table_name, e)
            raise
