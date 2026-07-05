#!/usr/bin/env python3
"""
SteamSpy生データ → stats.json 集計スクリプト
- AAA（推定オーナー500万以上）を除外したインディー層のみを分析
- レビュー数（positive+negative）を主軸指標とする（ownersはバケット推定で精度が低いため）
- 推定売上はBoxleiter法（レビュー数×35≒販売本数）による概算
"""
import json
import os
import sys
import hashlib
import statistics
from datetime import datetime, timezone

RAW_DIR = sys.argv[1] if len(sys.argv) > 1 else "raw"
OUT_PATH = sys.argv[2] if len(sys.argv) > 2 else "data/stats.json"

BOXLEITER_MULT = 35  # レビュー1件 ≒ 販売35本（業界で使われる中央的な係数）
AAA_OWNERS_THRESHOLD = 5_000_000

# 大手パブリッシャー（部分一致・小文字比較）— オーナー数フィルターをすり抜けるAAA対策
AAA_PUBLISHERS = [
    "electronic arts", "ea games", "ubisoft", "activision", "blizzard",
    "square enix", "capcom", "bandai namco", "sega", "bethesda",
    "2k", "rockstar", "cd projekt", "fromsoftware", "warner bros",
    "xbox game studios", "microsoft", "sony", "playstation", "konami",
    "koei tecmo", "take-two", "505 games", "deep silver", "thq nordic",
    "focus entertainment", "focus home", "nexon", "netease", "tencent",
    "epic games", "valve", "wb games", "rocksteady",
    "private division", "crytek", "io interactive",
]


def is_aaa_publisher(g):
    pub = (g.get("publisher") or "").lower()
    dev = (g.get("developer") or "").lower()
    return any(p in pub or p in dev for p in AAA_PUBLISHERS)

GENRES = [
    {"en": "Rogue-like",     "ja": "ローグライク",       "file": "Rogue-like"},
    {"en": "Rogue-lite",     "ja": "ローグライト",       "file": "Rogue-lite"},
    {"en": "Metroidvania",   "ja": "メトロイドヴァニア", "file": "Metroidvania"},
    {"en": "Platformer",     "ja": "プラットフォーマー", "file": "Platformer"},
    {"en": "Action RPG",     "ja": "アクションRPG",      "file": "Action_RPG"},
    {"en": "Survival",       "ja": "サバイバル",         "file": "Survival"},
    {"en": "Tower Defense",  "ja": "タワーディフェンス", "file": "Tower_Defense"},
    {"en": "Puzzle",         "ja": "パズル",             "file": "Puzzle"},
    {"en": "Visual Novel",   "ja": "ビジュアルノベル",   "file": "Visual_Novel"},
    {"en": "Horror",         "ja": "ホラー",             "file": "Horror"},
    {"en": "Sandbox",        "ja": "サンドボックス",     "file": "Sandbox"},
    {"en": "Strategy",       "ja": "ストラテジー",       "file": "Strategy"},
    {"en": "Simulation",     "ja": "シミュレーション",   "file": "Simulation"},
    {"en": "Fighting",       "ja": "格闘",               "file": "Fighting"},
    {"en": "Shoot 'Em Up",   "ja": "シューティング",     "file": "Shoot_Em_Up"},
]


def parse_owners(s):
    if not s:
        return 0
    parts = s.replace(",", "").split("..")
    try:
        lo = int(parts[0].strip())
        hi = int(parts[1].strip()) if len(parts) > 1 else lo
        return (lo + hi) // 2
    except (ValueError, IndexError):
        return 0


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def calc_score(g):
    pos, neg = to_int(g.get("positive")), to_int(g.get("negative"))
    total = pos + neg
    if total < 10:
        return None
    return round(pos / total * 100)


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def aggregate():
    # フォールバック検出: 同一ハッシュのファイルが複数あればフォールバック
    hashes = {}
    for g in GENRES:
        path = os.path.join(RAW_DIR, f"{g['file']}.json")
        if os.path.exists(path):
            h = file_hash(path)
            hashes.setdefault(h, []).append(g["file"])
    fallback_hashes = {h for h, files in hashes.items() if len(files) > 1}

    result = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": {
            "boxleiter_mult": BOXLEITER_MULT,
            "aaa_threshold": AAA_OWNERS_THRESHOLD,
            "note": "推定売上=レビュー数×35×価格。推定オーナー500万以上はAAAとして除外。",
        },
        "genres": [],
    }

    for g in GENRES:
        path = os.path.join(RAW_DIR, f"{g['file']}.json")
        entry = {"en": g["en"], "ja": g["ja"], "id": g["file"], "valid": False}

        if not os.path.exists(path):
            entry["error"] = "data_missing"
            result["genres"].append(entry)
            continue
        if file_hash(path) in fallback_hashes:
            entry["error"] = "fallback_detected"
            result["genres"].append(entry)
            continue

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            entry["error"] = "parse_error"
            result["genres"].append(entry)
            continue

        games = list(data.values())
        if len(games) < 10:
            entry["error"] = "too_few_games"
            result["genres"].append(entry)
            continue

        # インディー層 = AAA除外（オーナー数 + 大手パブリッシャーの二重フィルター）
        indie = [
            x for x in games
            if 0 < parse_owners(x.get("owners", "")) < AAA_OWNERS_THRESHOLD
            and not is_aaa_publisher(x)
        ]
        aaa_count = len(games) - len(indie)

        reviews = [to_int(x.get("positive")) + to_int(x.get("negative")) for x in indie]
        prices = [to_int(x.get("price")) for x in indie]
        paid_prices = [p for p in prices if p > 0]
        scores = [s for x in indie if (s := calc_score(x)) is not None]

        # 推定売上（有料・レビュー10件以上のみ）
        revenues = []
        for x in indie:
            r = to_int(x.get("positive")) + to_int(x.get("negative"))
            p = to_int(x.get("price"))
            if r >= 10 and p > 0:
                revenues.append(r * BOXLEITER_MULT * p)

        hit1000 = sum(1 for r in reviews if r >= 1000)

        # レビュー数分布
        rd = [0, 0, 0, 0, 0]
        for r in reviews:
            if r < 50:
                rd[0] += 1
            elif r < 200:
                rd[1] += 1
            elif r < 1000:
                rd[2] += 1
            elif r < 5000:
                rd[3] += 1
            else:
                rd[4] += 1

        # 価格分布
        pd = [0, 0, 0, 0, 0, 0]
        for p in prices:
            if p == 0:
                pd[0] += 1
            elif p <= 500:
                pd[1] += 1
            elif p <= 1000:
                pd[2] += 1
            elif p <= 2000:
                pd[3] += 1
            elif p <= 4000:
                pd[4] += 1
            else:
                pd[5] += 1

        # トップ12（レビュー数順 = 実際に売れている順）
        top = sorted(indie, key=lambda x: to_int(x.get("positive")) + to_int(x.get("negative")), reverse=True)[:12]
        top_list = []
        for x in top:
            r = to_int(x.get("positive")) + to_int(x.get("negative"))
            p = to_int(x.get("price"))
            top_list.append({
                "name": x.get("name", ""),
                "dev": x.get("developer", ""),
                "reviews": r,
                "score": calc_score(x),
                "price": p,
                "est_revenue": r * BOXLEITER_MULT * p if p > 0 else 0,
            })

        entry.update({
            "valid": True,
            "count": len(indie),
            "aaa_excluded": aaa_count,
            "median_reviews": int(statistics.median(reviews)) if reviews else 0,
            "hit1000_rate": round(hit1000 / len(indie) * 100, 1) if indie else 0,
            "avg_score": round(statistics.mean(scores)) if scores else None,
            "free_ratio": round(prices.count(0) / len(prices) * 100) if prices else 0,
            "median_price": int(statistics.median(paid_prices)) if paid_prices else 0,
            "est_revenue_median": int(statistics.median(revenues)) if revenues else 0,
            "review_dist": rd,
            "price_dist": pd,
            "top": top_list,
        })
        result["genres"].append(entry)
        print(f"OK  {g['en']:<16} indie={len(indie):>6} hit1000={entry['hit1000_rate']}% medRev={entry['median_reviews']}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT_PATH)
    print(f"\nstats.json generated: {size:,} bytes ({size/1024:.1f} KB)")


if __name__ == "__main__":
    aggregate()
