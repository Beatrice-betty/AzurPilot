-- 月度统计恢复按实例与月份保存完整记录。
CREATE TABLE cl1_data (
    instance TEXT NOT NULL REFERENCES storage_instances(instance),
    month TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY(instance, month)
) STRICT;
