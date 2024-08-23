# Databricks notebook source
# MAGIC %md
# MAGIC # Lakehouse Monitoring Demo
# MAGIC
# MAGIC ## Time-series Monitor
# MAGIC
# MAGIC ### Use case
# MAGIC Let's explore a retail use case where one of the most important layers is the `silver_transaction` table that joins data from upstream bronze tables and impacts downstream gold tables. The data schema used in the demo is as follows: 
# MAGIC
# MAGIC <img src="https://raw.githubusercontent.com/databricks-demos/dbdemos-resources/main/images/product/lhm/lhm_data.png" width="600px" style="float:right"/>
# MAGIC
# MAGIC Data analysts are using these tables to generate reports and make various business decisions. Most recently, an analyst is trying to determine the most popular `PreferredPaymentMethod`. When querying the `silver_transaction` table, they discover that there's been a number of transactions with `null` `PreferredPaymentMethod` and shares a screenshot of the problem:
# MAGIC
# MAGIC ![image3](https://github.com/databricks-demos/dbdemos-resources/blob/main/images/product/lhm/lhm_payment_type.png?raw=true)
# MAGIC
# MAGIC At this point, you may be asking yourself a number of questions such as: 
# MAGIC 1. What percent of `nulls` have been introduced to this column? Is this normal? 
# MAGIC 2. If it's not normal, what was the root cause for this integrity issue? 
# MAGIC 3. What are the downstream assets that might've been impacted by this issue?
# MAGIC
# MAGIC Let's explore how Lakehouse Monitoring ([AWS](https://docs.databricks.com/en/lakehouse-monitoring/index.html) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/lakehouse-monitoring/)) can help you answer these types of questions. 

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Install Dependencies

# COMMAND ----------

# MAGIC %pip install "databricks-sdk>=0.28.0"
# MAGIC
# MAGIC
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_resources/01-DataGeneration

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. View the dataset
# MAGIC

# COMMAND ----------

# MAGIC %sql 
# MAGIC -- To setup monitoring, load in the silver_transaction dataset
# MAGIC SELECT * from silver_transaction limit 10;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Create the monitor
# MAGIC
# MAGIC To create a monitor, we can choose from three different types of profile types: 
# MAGIC 1. **Timeseries**: Aggregates quality metrics over time windows
# MAGIC 2. **Snapshot**: Calculates quality metrics over the full table
# MAGIC 3. **Inference**: Tracks model drift and performance over time
# MAGIC
# MAGIC Since we are monitoring transaction data and have a timestamp column in the table, a Timeseries works best in this scenario. For other types of analysis, see the Lakehouse Monitoring documentation ([AWS](https://docs.databricks.com/en/lakehouse-monitoring/create-monitor-ui.html#profiling) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/lakehouse-monitoring/create-monitor-ui#profiling)).

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import MonitorTimeSeries
import os

# COMMAND ----------

# Define time windows to aggregate metrics over
GRANULARITIES = ["1 day"]                       

# Optionally define expressions to slice data with
SLICING_EXPRS = ["Category='Toys'"]  

# COMMAND ----------

# You must have `USE CATALOG` privileges on the catalog, and you must have `USE SCHEMA` privileges on the schema.
# If necessary, change the catalog and schema name here.
TABLE_NAME = f"{catalog}.{dbName}.silver_transaction"

# Define the timestamp column name
TIMESTAMP_COL = "TransactionDate"

# Enable Change Data Feed (CDF) to incrementally process changes to the table and make execution more efficient 
display(spark.sql(f"ALTER TABLE {TABLE_NAME} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)"))

# COMMAND ----------

w = WorkspaceClient()

# COMMAND ----------

# Create a monitor using a Timeseries profile type. After the intial refresh completes, you can view the autogenerated dashboard from the Quality tab of the table in Catalog Explorer. 
print(f"Creating monitor for {TABLE_NAME}")

w = WorkspaceClient()

try:
  lhm_monitor = w.quality_monitors.create(
    table_name=TABLE_NAME, # Always use 3-level namespace
    time_series = MonitorTimeSeries(
      timestamp_col=TIMESTAMP_COL,
      granularities=GRANULARITIES
    ),
    assets_dir = os.getcwd(),
    output_schema_name=f"{catalog}.{dbName}"
  )

except Exception as lhm_exception:
  print(lhm_exception)

# COMMAND ----------

# MAGIC %md
# MAGIC This next step waits for the monitor to be created and then waits for the initial calculation of metrics to complete.
# MAGIC
# MAGIC Note: It can take 10+ minutes to create and refresh the monitor

# COMMAND ----------

import time
from databricks.sdk.service.catalog import MonitorInfoStatus, MonitorRefreshInfoState

# COMMAND ----------

# Wait for monitor to be created
info = w.quality_monitors.get(table_name=f"{TABLE_NAME}")
while info.status == MonitorInfoStatus.MONITOR_STATUS_PENDING:
  info = w.quality_monitors.get(table_name=f"{TABLE_NAME}")
  time.sleep(10)

assert info.status == MonitorInfoStatus.MONITOR_STATUS_ACTIVE, "Error creating monitor"

refreshes = w.quality_monitors.list_refreshes(table_name=f"{TABLE_NAME}").refreshes
assert(len(refreshes) > 0)

run_info = refreshes[0]
while run_info.state in (MonitorRefreshInfoState.PENDING, MonitorRefreshInfoState.RUNNING):
  run_info = w.quality_monitors.get_refresh(table_name=f"{TABLE_NAME}", refresh_id=run_info.refresh_id)
  time.sleep(30)

assert run_info.state == MonitorRefreshInfoState.SUCCESS, "Monitor refresh failed"

# COMMAND ----------

# MAGIC %md ### Orientation to the profile metrics table
# MAGIC
# MAGIC The profile metrics table has the suffix `_profile_metrics`. For a list of statistics that are shown in the table, see the documentation ([AWS](https://docs.databricks.com/lakehouse-monitoring/monitor-output.html#profile-metrics-table)|[Azure](https://learn.microsoft.com/azure/databricks/lakehouse-monitoring/monitor-output#profile-metrics-table)). 
# MAGIC
# MAGIC - For every column in the primary table, the profile table shows summary statistics for the baseline table and for the primary table. The column `log_type` shows `INPUT` to indicate statistics for the primary table, and `BASELINE` to indicate statistics for the baseline table. The column from the primary table is identified in the column `column_name`.
# MAGIC - For `TimeSeries` type analysis, the `granularity` column shows the granularity corresponding to the row. For baseline table statistics, the `granularity` column shows `null`.
# MAGIC - The table shows statistics for each value of each slice key in each time window, and for the table as whole. Statistics for the table as a whole are indicated by `slice_key` = `slice_value` = `null`.
# MAGIC - In the primary table, the `window` column shows the time window corresponding to that row. For baseline table statistics, the `window` column shows `null`.  
# MAGIC - Some statistics are calculated based on the table as a whole, not on a single column. In the column `column_name`, these statistics are identified by `:table`.
# MAGIC

# COMMAND ----------

# Display profile metrics table
profile_table = lhm_monitor.profile_metrics_table_name  
display(spark.sql(f"SELECT * FROM {profile_table}"))

# COMMAND ----------

# MAGIC %md ### Orientation to the drift metrics table
# MAGIC
# MAGIC The drift metrics table has the suffix `_drift_metrics`. For a list of statistics that are shown in the table, see the documentation ([AWS](https://docs.databricks.com/lakehouse-monitoring/monitor-output.html#drift-metrics-table) | [Azure](https://learn.microsoft.com/azure/databricks/lakehouse-monitoring/monitor-output#drift-metrics-table)). 
# MAGIC
# MAGIC - For every column in the primary table, the drift table shows a set of metrics that compare the current values in the table to the values at the time of the previous analysis run and to the baseline table. The column `drift_type` shows `BASELINE` to indicate drift relative to the baseline table, and `CONSECUTIVE` to indicate drift relative to a previous time window. As in the profile table, the column from the primary table is identified in the column `column_name`.
# MAGIC - For `TimeSeries` type analysis, the `granularity` column shows the granularity corresponding to that row.
# MAGIC - The table shows statistics for each value of each slice key in each time window, and for the table as whole. Statistics for the table as a whole are indicated by `slice_key` = `slice_value` = `null`.
# MAGIC - The `window` column shows the the time window corresponding to that row. The `window_cmp` column shows the comparison window. If the comparison is to the baseline table, `window_cmp` is `null`.  
# MAGIC - Some statistics are calculated based on the table as a whole, not on a single column. In the column `column_name`, these statistics are identified by `:table`.

# COMMAND ----------

# Display the drift metrics table
drift_table = lhm_monitor.drift_metrics_table_name  
display(spark.sql(f"SELECT * FROM {drift_table}"))

# COMMAND ----------

# MAGIC %md
# MAGIC Let's view the profile metrics that are calculated for the individual TotalPurchaseAmount column. Note that metrics are calculated across the table and for individual columns. We'll see this in more detail when we add custom metrics.

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {profile_table} where column_name = 'TotalPurchaseAmount'"))

# COMMAND ----------

# MAGIC %md
# MAGIC And let's view the drift metrics that are calculated for the `TotalPurchaseAmount` column.

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {drift_table} where column_name = 'TotalPurchaseAmount'"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. View the Autogenerated Dashboard
# MAGIC After the intial refresh completes, you can view the autogenerated dashboard from the Quality tab of the `silver_transactions` table in Catalog Explorer. The dashboard visualizes metrics in the following sections: 
# MAGIC 1. **Data Volume**: Check if transaction volume is expected or if there's been changes with seasonality
# MAGIC 2. **Data Integrity**: Identify the columns with a high % of nulls or zeros and view their distribution over time
# MAGIC 3. **Numerical Distribution Change**: Identify numerical anomalies and view the Range of values over time
# MAGIC 4. **Categorical Distribution Change**: Identify categorical anomalies like `PreferredPaymentMethod` and view the distribution of values time
# MAGIC 5. **Profiling**: Explore the numerical and categorical data profile over time
# MAGIC
# MAGIC <img src="https://github.com/databricks-demos/dbdemos-resources/blob/main/images/product/lhm/lhm_dashboard-1.png?raw=true" width="800px" style="float:right"/>
# MAGIC

# COMMAND ----------

# MAGIC %md
# MAGIC ## Dashboard Investigation
# MAGIC
# MAGIC Without any additional tools or complexity, Lakehouse Monitoring allows you to easily profile, diagnose, and enforce quality directly in the Databricks Data Intelligence Platform. 
# MAGIC
# MAGIC Based on the dashboard, we can answer the 3 questions that we originally had: 
# MAGIC
# MAGIC 1. What percent of nulls have been introduced to this column? Is this normal?
# MAGIC > From the % Nulls section, we can see that `PreferredPaymentMethod` spiked from 10% to around 40%. 
# MAGIC 2. If it's not normal, what was the root cause for this integrity issue?
# MAGIC > Using the Categorical Distribution Change section, we can see that both `PreferredPaymentMethod` and `PaymentMethod` had high drift in the last time window. With the heatmap, we can discover that Apple Pay was recently added as a new `PaymentMethod` at the same time `nulls` started to appear in `PreferredPaymentMethod`
# MAGIC <img src="https://github.com/databricks-demos/dbdemos-resources/blob/main/images/product/lhm/lhm_dashboard-2.png?raw=true" width="800px" style="float:right"/>
# MAGIC 3. What are the downstream assets that might've been impacted by this issue?
# MAGIC > Since Lakehouse Monitoring is built on top of Unity Catalog, you can use the Lineage Graph to identify downstream tables that have been impacted: 
# MAGIC <img src="https://github.com/databricks-demos/dbdemos-resources/blob/main/images/product/lhm/lhm_lineage.png?raw=true" width="800px" style="float:right"/>
# MAGIC
# MAGIC Like we explored in this demo, you can proactively discover quality issues before downstream processes are impacted. Get started with Lakehouse Monitoring (Generally Available) ([AWS](https://docs.databricks.com/en/lakehouse-monitoring/index.html)| [Azure](https://learn.microsoft.com/en-us/azure/databricks/lakehouse-monitoring/)) today and ensure reliability across your entire data + AI estate.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Create Custom Metrics

# COMMAND ----------

# MAGIC %md
# MAGIC Databricks Lakehouse Monitoring includes the following types of custom metrics:
# MAGIC
# MAGIC **Aggregate metrics**, which are calculated based on columns in the primary table. Aggregate metrics are stored in the profile metrics table.
# MAGIC
# MAGIC **Derived metrics**, which are calculated based on previously computed aggregate metrics and do not directly use data from the primary table. Derived metrics are stored in the profile metrics table.
# MAGIC
# MAGIC **Drift metrics**, which compare previously computed aggregate or derived metrics from two different time windows, or between the primary table and the baseline table. Drift metrics are stored in the drift metrics table.
# MAGIC
# MAGIC Using derived and drift metrics where possible minimizes recomputation over the full primary table. Only aggregate metrics access data from the primary table. Derived and drift metrics can then be computed directly from the aggregate metric values.
# MAGIC
# MAGIC ([AWS](https://docs.databricks.com/en/lakehouse-monitoring/custom-metrics.html) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/lakehouse-monitoring/custom-metrics))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Aggergate and Derived metrics
# MAGIC
# MAGIC These metrics can be calculated either on individual columns or as a combination (of user-defined) columns.

# COMMAND ----------

# MAGIC %md
# MAGIC #### Combination of mutiple columns
# MAGIC We'll create a metric to calculate the average of the price after discount. The price after discount is calculated as `Price * (1 - Discount)`. This metric will appear as a new column in the profile metrics table. We will use the `Price` and `Discount` fields from the table, so it will need to include `:table` as the input_column. You'll use `:table` whenever you need to use multiple fields in your calculation

# COMMAND ----------

from databricks.sdk.service.catalog import MonitorMetric, MonitorMetricType
import pyspark.sql.types as T

# COMMAND ----------

avg_price_after_discount_agg = MonitorMetric(
    type=MonitorMetricType.CUSTOM_METRIC_TYPE_AGGREGATE,
    name="avg_price_after_discount",
    input_columns=[":table"],
    definition="avg(Price*(1-Discount))",
    output_data_type=T.StructField("avg_price_after_discount", T.DoubleType()).json(),
)

# COMMAND ----------

avg_price_after_discount_agg.definition

# COMMAND ----------

# MAGIC %md
# MAGIC Next, we'll create a **derived metric**. Note that it uses `avg_price_after_discount` in the definition. 

# COMMAND ----------

log_price_after_discount_derived = MonitorMetric(
    type=MonitorMetricType.CUSTOM_METRIC_TYPE_DERIVED,
    name="log_price_after_discount",
    input_columns=[":table"],
    definition="log(avg_price_after_discount)",
    output_data_type=T.StructField("log_price_after_discount", T.DoubleType()).json(),
)

# COMMAND ----------

# MAGIC %md
# MAGIC #### Metrics on individual column(s)
# MAGIC We can also create metrics that can be calulated for individual fields and we can specify which of these fields that we want to use by passing them into `input_columns`, and then they will be used in the definition with `{{input_column}}`. Note that the calculation will be performed separately for each input column and `{{input_column}}` will only refer to the value for the one field that the metric is being calculated for. In this example, we'll create a metric that calculates the variance of a field and apply it to `TotalPurchaseAmount` and `Discount`. So, we independently calculate the variance of the `TotalPurchaseAmount` and  `Discount` fields.

# COMMAND ----------

variance_metric_agg = MonitorMetric(
    type=MonitorMetricType.CUSTOM_METRIC_TYPE_AGGREGATE,
    name="variance",
    input_columns=["TotalPurchaseAmount", "Discount"],
    definition="var_samp(`{{input_column}}`)",
    output_data_type=T.StructField("variance", T.DoubleType()).json(),
)

# COMMAND ----------

# MAGIC %md
# MAGIC We'll also demonstrate creating the standard deviation by using the variance metric that we just created. Note that stddev is already included in the default metrics, and this is for illustration purposes. This is done by creating a derived metric. Note that **derived metrics cannot access template items like `{{input_column}}` in their definitions.** This metric uses `variance` which was calculated for the `TotalPurchaseAmount` and `Discount` fields, so we can use them here as input columns.
# MAGIC
# MAGIC **Notice that we pass in `["TotalPurchaseAmount", "Discount"]` as `input_columns` as well**. If you use `:table` as `input_columns`, you need to specify the column names in calculating the metric, i.e. you'd need to create a metric for `TotalPurchaseAmountStd` and the following drift metrics. The steps would need to be repeated for the `Discount` column again. Specifying `input_columns=["TotalPurchaseAmount", "Discount"]` is more efficient.

# COMMAND ----------

std_metric_derived = MonitorMetric(
    type=MonitorMetricType.CUSTOM_METRIC_TYPE_DERIVED,
    name="std",
    input_columns=["TotalPurchaseAmount", "Discount"],
    definition="sqrt(variance)",
    output_data_type=T.StructField("std", T.DoubleType()).json(),
)

# COMMAND ----------

# MAGIC %md ### Drift Metrics
# MAGIC

# COMMAND ----------

# MAGIC %md
# MAGIC Next let's build a drift metrics that use our derived standard deviation metric and calculate the percentage difference across our `TotalPurchaseAmount` and `Discount` fields.

# COMMAND ----------

std_delta_pct_drift = MonitorMetric(
    type=MonitorMetricType.CUSTOM_METRIC_TYPE_DRIFT,
    name="std_delta_pct",
    input_columns=["TotalPurchaseAmount", "Discount"],
    definition="100*({{current_df}}.std - {{base_df}}.std)/{{base_df}}.std",
    output_data_type=T.StructField("std_pct_delta", T.DoubleType()).json(),
)

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC Let's create a custom drift metric that calulate the difference between our current value for the standard deviation of the price after discount and the base value.

# COMMAND ----------

log_price_after_discount_delta_drift = MonitorMetric(
    type=MonitorMetricType.CUSTOM_METRIC_TYPE_DRIFT,
    name="log_price_after_discount_ratio",
    input_columns=[":table"],
    definition="{{current_df}}.log_price_after_discount/{{base_df}}.log_price_after_discount",
    output_data_type=T.StructField("log_price_after_discount_delta", T.DoubleType()).json(),
)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Update the Monitor
# MAGIC Let's update the monitor and view the results.

# COMMAND ----------

try:
    lhm_monitor = w.quality_monitors.update(
        table_name=TABLE_NAME,  # Always use 3-level namespace
        time_series=MonitorTimeSeries(
            timestamp_col=TIMESTAMP_COL, granularities=GRANULARITIES
        ),
        custom_metrics=[
            avg_price_after_discount_agg,
            log_price_after_discount_derived,
            log_price_after_discount_delta_drift,
            variance_metric_agg,
            std_metric_derived,
            std_delta_pct_drift,
        ],
        output_schema_name=f"{catalog}.{dbName}",
    )

except Exception as lhm_exception:
    print(lhm_exception)

# COMMAND ----------

# MAGIC %md
# MAGIC Note that updating the monitor does not cause it to be refreshed. We also need to request a refresh.

# COMMAND ----------

# Refresh the table to calculate the custom metrics
run_info = w.quality_monitors.run_refresh(table_name=TABLE_NAME)

w = WorkspaceClient()

while run_info.state in (MonitorRefreshInfoState.PENDING, MonitorRefreshInfoState.RUNNING):
  run_info = w.quality_monitors.get_refresh(table_name=f"{TABLE_NAME}", refresh_id=run_info.refresh_id)
  time.sleep(30)

assert run_info.state == MonitorRefreshInfoState.SUCCESS, "Monitor refresh failed"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Query Custom Metrics from Profile and Drift Metrics Table
# MAGIC
# MAGIC Once the refresh has finished our profile and drift tables will be updated to include our custom variables. Note that for variables that used `:table` as input, you can see them by filtering by `column_name = ':table'`. Metrics that are calculated on a single field can filtered for using the field name, for example, `column_name = 'TotalPurchasedAmount'`. For your custom metrics you can also use SQL to filter by where that metric isn't null. For example, `where variance is not null`. The aggregate and derived metrics show up as new fields in the profile table and the drift metrics show up in the drift table. 

# COMMAND ----------

# MAGIC %md
# MAGIC Let's take a look at our profile table to see our table-based custom metrics

# COMMAND ----------

display(spark.sql(f"SELECT window, avg_price_after_discount, log_price_after_discount FROM {profile_table} WHERE column_name = ':table' ORDER BY window.start;"))

# COMMAND ----------

# MAGIC %md
# MAGIC And the custom aggregate & derived metric that we created on the individual columns (e.g. `TotalPurchaseAmount` and `Discount`)

# COMMAND ----------


display(spark.sql(f"SELECT window, column_name, variance, std FROM {profile_table} WHERE variance IS NOT NULL ORDER BY window.start, column_name"))

# COMMAND ----------

# MAGIC %md
# MAGIC And the corresponding drift metrics from the drift table.

# COMMAND ----------

# Let's look at our custom drift metrics
display(spark.sql(f"SELECT window, window_cmp, log_price_after_discount_ratio FROM {drift_table} WHERE column_name = ':table' ORDER BY window.start;"))

# COMMAND ----------

display(spark.sql(f"SELECT window, window_cmp, std_delta_pct FROM {drift_table} WHERE std_delta_pct IS NOT NULL ORDER BY window.start;"))

# COMMAND ----------

# Uncomment the following line of code to clean up the monitor (if you wish to run the quickstart on this table again).
# w.quality_monitors.delete(table_name=TABLE_NAME)

# COMMAND ----------

# MAGIC %md 
# MAGIC ## Next step - conclusion
# MAGIC
# MAGIC We saw how to leverage Databricks to monitor your existing table and data.
# MAGIC
# MAGIC But Databricks Monitors can do more! 
# MAGIC
# MAGIC Open the next [02-Inference-monitor]($./02-Inference-monitor) notebook to see how to monitor your Machine Learning models from Inference tables!
