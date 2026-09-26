"""接力队成绩核算测试。夹具 client 见 conftest.py。

核算完全基于已发布版本快照，默认 6 站（5 棒 / 4 个交接站）。
"""
from __future__ import annotations


def post(client, event_no, runner, station, occurred_at, corrects=None):
    body = {
        "event_no": event_no,
        "runner_id": runner,
        "station_seq": station,
        "occurred_at": occurred_at,
    }
    if corrects is not None:
        body["corrects"] = corrects
    return client.post("/api/checkins", json=body)


def relay(client, version, runner_ids):
    return client.post(
        f"/api/publications/{version}/relay", json={"runner_ids": runner_ids}
    )


def add_relay_runners(client, times):
    """times: runner_id -> {站点序号: ISO 时间}；批量登记后发布，返回新版本号。"""
    checkins = [
        {
            "event_no": f"T-{rid}-{seq}",
            "runner_id": rid,
            "station_seq": seq,
            "occurred_at": t,
        }
        for rid, stations in times.items()
        for seq, t in stations.items()
    ]
    r = client.post("/api/checkins/batch", json={"checkins": checkins})
    assert r.status_code == 201, r.text
    r = client.post("/api/publications")
    assert r.status_code == 201
    return r.json()["version"]


# 每名接力选手仅在承担赛段的两端打卡；时间链为
# 08:00 →(30m)→ 08:30 →(10m)→ 08:40 →(20m)→ 09:00 →(5m)→ 09:05 →(15m)→ 09:20
FULL_CHAIN = {
    "A1": {1: "2026-09-25T08:00:00", 2: "2026-09-25T08:30:00"},
    "A2": {2: "2026-09-25T08:30:00", 3: "2026-09-25T08:40:00"},
    "A3": {3: "2026-09-25T08:40:00", 4: "2026-09-25T09:00:00"},
    "A4": {4: "2026-09-25T09:00:00", 5: "2026-09-25T09:05:00"},
    "A5": {5: "2026-09-25T09:05:00", 6: "2026-09-25T09:20:00"},
}


# ---------- 成功核算 ----------


def test_relay_full_valid_team_zero_waits(client):
    version = add_relay_runners(client, FULL_CHAIN)
    r = relay(client, version, ["A1", "A2", "A3", "A4", "A5"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["complete"] is True
    assert data["version"] == version
    assert data["station_count"] == 6

    assert [leg["runner_id"] for leg in data["legs"]] == [
        "A1", "A2", "A3", "A4", "A5"
    ]
    assert [leg["duration_seconds"] for leg in data["legs"]] == [
        1800, 600, 1200, 300, 900
    ]
    assert all(leg["status"] == "ok" for leg in data["legs"])
    # 交接全部同时：等待为 0
    assert [h["wait_seconds"] for h in data["handoffs"]] == [0, 0, 0, 0]
    # 总用时 = 各棒之和（4800 秒 = 1:20:00）= 首棒起点至末棒终点
    assert data["total_seconds"] == 4800
    assert data["total"] == "1:20:00"
    # 逐棒两端入选打卡取自版本快照
    assert data["legs"][0]["start"]["event_no"] == "T-A1-1"
    assert data["legs"][-1]["finish"]["event_no"] == "T-A5-6"
    assert data["handoffs"][0]["arrival"]["event_no"] == "T-A1-2"
    assert data["handoffs"][0]["departure"]["event_no"] == "T-A2-2"


def test_relay_total_equals_legs_plus_waits(client):
    chain = {
        "B1": {1: "2026-09-25T08:00:00", 2: "2026-09-25T08:30:00"},
        # 第 2 人晚 10 分钟出发
        "B2": {2: "2026-09-25T08:40:00", 3: "2026-09-25T09:10:00"},
        # 第 3 人晚 5 分钟出发
        "B3": {3: "2026-09-25T09:15:00", 4: "2026-09-25T10:00:00"},
        "B4": {4: "2026-09-25T10:00:00", 5: "2026-09-25T10:05:00"},
        "B5": {5: "2026-09-25T10:05:00", 6: "2026-09-25T10:30:00"},
    }
    version = add_relay_runners(client, chain)
    data = relay(client, version, ["B1", "B2", "B3", "B4", "B5"]).json()
    assert data["complete"] is True
    legs = [l["duration_seconds"] for l in data["legs"]]
    waits = [h["wait_seconds"] for h in data["handoffs"]]
    assert legs == [1800, 1800, 2700, 300, 1500]
    assert waits == [600, 300, 0, 0]
    assert data["total_seconds"] == sum(legs) + sum(waits)
    # 首棒起点 08:00 至末棒终点 10:30 = 2:30:00 = 9000
    assert data["total_seconds"] == 9000
    assert data["total"] == "2:30:00"


# ---------- 404 / 422 ----------


def test_relay_version_not_found_404(client):
    # 连一次发布都没有
    r = relay(client, 1, ["R001", "R002", "R001", "R002", "R001"])
    assert r.status_code == 404
    client.post("/api/publications")
    assert relay(client, 999, ["R001"] * 5).status_code == 404


def test_relay_wrong_member_count_422(client):
    client.post("/api/publications")  # v1，6 站需 5 人
    assert relay(client, 1, ["R001", "R002", "R003", "R004"]).status_code == 422
    assert relay(
        client, 1, ["R001", "R002", "R003", "R004", "R005", "R006"]
    ).status_code == 422


def test_relay_duplicate_runners_422(client):
    client.post("/api/publications")  # v1
    r = relay(client, 1, ["R001", "R001", "R002", "R002", "R003"])
    assert r.status_code == 422
    assert "R001" in r.json()["detail"]
    assert "R002" in r.json()["detail"]


def test_relay_empty_runner_id_422(client):
    client.post("/api/publications")
    r = relay(client, 1, ["", "R002", "R003", "R004", "R005"])
    assert r.status_code == 422


# ---------- 队伍待定 ----------


def test_relay_runner_not_in_version_pending(client):
    version = add_relay_runners(client, FULL_CHAIN)
    data = relay(client, version, ["A1", "A2", "GHOST", "A4", "A5"]).json()
    assert data["complete"] is False
    assert data["total_seconds"] is None
    assert data["total"] is None
    legs = {l["leg"]: l for l in data["legs"]}
    assert legs[3]["status"] == "pending"
    assert legs[3]["duration_seconds"] is None
    assert "GHOST" in legs[3]["reason"]
    assert "不在成绩版本内" in legs[3]["reason"]
    # 未受影响的棒次仍给出用时与原因 null
    assert legs[1]["status"] == "ok"
    assert legs[1]["duration_seconds"] == 1800
    assert legs[1]["reason"] is None


def test_relay_runner_segment_pending_in_snapshot(client):
    # C3 缺第 4 站：分段 3→4 待定；交接时间刻意安排为合法
    chain = {
        "C1": {1: "2026-09-25T08:00:00", 2: "2026-09-25T08:30:00"},
        "C2": {2: "2026-09-25T08:30:00", 3: "2026-09-25T08:40:00"},
        "C3": {3: "2026-09-25T08:40:00", 5: "2026-09-25T09:00:00"},
        "C4": {4: "2026-09-25T09:00:00", 5: "2026-09-25T09:05:00"},
        "C5": {5: "2026-09-25T09:05:00", 6: "2026-09-25T09:20:00"},
    }
    version = add_relay_runners(client, chain)
    data = relay(client, version, ["C1", "C2", "C3", "C4", "C5"]).json()
    assert data["complete"] is False
    assert data["total_seconds"] is None
    legs = {l["leg"]: l for l in data["legs"]}
    assert legs[3]["status"] == "pending"
    assert legs[3]["finish"] is None
    assert legs[3]["start"]["event_no"] == "T-C3-3"
    assert "站点 4 缺少有效打卡" in legs[3]["reason"]
    # 该棒两端的交接也待定：上一棒到达站 4 的打卡不存在
    handoffs = {h["station_seq"]: h for h in data["handoffs"]}
    assert handoffs[4]["status"] == "pending"
    assert handoffs[4]["wait_seconds"] is None
    assert "未到达站点 4" in handoffs[4]["reason"]
    # 其余交接仍可核算
    assert handoffs[3]["status"] == "ok"
    assert handoffs[3]["wait_seconds"] == 0


def test_relay_early_departure_pending_on_seed_version(client):
    # 种子版本 v1：R001、R002、R004、R006、R003
    # 交接 2/3/4 等待：13 / 5 / 15 分钟；交接 5 为 -5 分钟（非法）
    client.post("/api/publications")
    data = relay(client, 1, ["R001", "R002", "R004", "R006", "R003"]).json()
    assert data["complete"] is False
    assert data["total_seconds"] is None

    legs = {l["leg"]: l for l in data["legs"]}
    assert legs[1]["duration_seconds"] == 32 * 60   # R001 08:00→08:32
    assert legs[2]["duration_seconds"] == 30 * 60   # R002 08:45→09:15
    assert legs[3]["duration_seconds"] == 45 * 60   # R004 09:20→10:05
    assert legs[4]["duration_seconds"] == 50 * 60   # R006 10:20→11:10
    # 第 5 棒在交接站 5 出发（R003 11:05）早于第 4 棒到达（R006 11:10）→ 整棒待定
    assert legs[5]["status"] == "pending"
    assert legs[5]["runner_id"] == "R003"
    assert legs[5]["duration_seconds"] is None
    assert "交接站 5" in legs[5]["reason"]
    assert "早于" in legs[5]["reason"]

    handoffs = {h["station_seq"]: h for h in data["handoffs"]}
    assert handoffs[2]["status"] == "ok"
    assert handoffs[2]["wait_seconds"] == 13 * 60
    assert handoffs[3]["status"] == "ok"
    assert handoffs[3]["wait_seconds"] == 5 * 60
    assert handoffs[4]["status"] == "ok"
    assert handoffs[4]["wait_seconds"] == 15 * 60
    assert handoffs[5]["status"] == "pending"
    assert handoffs[5]["wait_seconds"] is None
    assert handoffs[5]["arrival"]["event_no"] == "S-R006-5"
    assert handoffs[5]["departure"]["event_no"] == "S-R003-5"
    assert "早于" in handoffs[5]["reason"]


def test_relay_outgoing_leg_without_start_pending(client):
    # D4 不在站点 4 打卡（第 4 棒无法出发 / 交接站 4 无法核算）
    chain = {
        "D1": {1: "2026-09-25T08:00:00", 2: "2026-09-25T08:30:00"},
        "D2": {2: "2026-09-25T08:30:00", 3: "2026-09-25T08:40:00"},
        "D3": {3: "2026-09-25T08:40:00", 4: "2026-09-25T09:00:00"},
        "D4": {5: "2026-09-25T09:05:00"},
        "D5": {5: "2026-09-25T09:05:00", 6: "2026-09-25T09:20:00"},
    }
    version = add_relay_runners(client, chain)
    data = relay(client, version, ["D1", "D2", "D3", "D4", "D5"]).json()
    assert data["complete"] is False
    legs = {l["leg"]: l for l in data["legs"]}
    assert legs[4]["status"] == "pending"
    assert legs[4]["start"] is None
    handoffs = {h["station_seq"]: h for h in data["handoffs"]}
    assert handoffs[4]["status"] == "pending"
    assert "未从站点 4 出发" in handoffs[4]["reason"]


# ---------- 快照隔离 ----------


def test_relay_reads_snapshot_not_live_results(client):
    version = add_relay_runners(client, FULL_CHAIN)
    # 发布后改动实时成绩：撤销 A1 的第 2 站并补一条更晚的打卡
    assert client.post("/api/checkins/T-A1-2/revoke").status_code == 200
    post(client, "T-A1-2B", "A1", 2, "2026-09-25T08:35:00")

    data = relay(client, version, ["A1", "A2", "A3", "A4", "A5"]).json()
    # 仍按版本快照核算：第 1 棒 30 分钟、首交接等待 0，总用时不变
    assert data["complete"] is True
    assert data["legs"][0]["duration_seconds"] == 1800
    assert data["legs"][0]["finish"]["event_no"] == "T-A1-2"
    assert data["handoffs"][0]["wait_seconds"] == 0
    assert data["total_seconds"] == 4800


# ---------- 其他站点规模 ----------


def test_relay_member_count_taken_from_version_station_count(client, monkeypatch):
    """人数要求取自版本快照的站点数，而非当前 STATION_COUNT 环境变量。"""
    # 2 站版本：只需要 1 名选手
    monkeypatch.setenv("STATION_COUNT", "2")
    post(client, "X1", "X1", 1, "2026-09-25T08:00:00")
    post(client, "X2", "X1", 2, "2026-09-25T08:20:00")
    r = client.post("/api/publications")
    assert r.status_code == 201
    version = r.json()["version"]
    assert r.json()["station_count"] == 2

    r = relay(client, version, ["X1"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["station_count"] == 2
    assert len(data["legs"]) == 1
    assert len(data["handoffs"]) == 0
    assert data["complete"] is True
    assert data["total_seconds"] == 1200
    assert data["total"] == "0:20:00"

    # 多交 / 少交人都不合法
    assert relay(client, version, []).status_code == 422
    assert relay(client, version, ["X1", "X1"]).status_code == 422
