"""Step 40 - wind climate of Oran from real observations, and CFD inflow scenarios.

Source: NOAA Integrated Surface Database (ISD), station 60490099999
"ES SENIA, AG" (Oran - Es Senia airport, 35.624 N, 0.621 W, 90 m a.s.l.),
hourly files 2014-2024 (public mirror s3://noaa-global-hourly-pds).
Field WND = "ddd,q,T,ssss,q": direction (deg from), quality, type
(N normal, C calm, V variable, 9 missing), speed (m/s x10), quality.
Only quality codes 0/1/4/5/9-with-valid-value of QC'd sources are kept
(codes 2, 3, 6, 7 = suspect/erroneous are rejected). One value per hour
(METAR FM-15 preferred, then SYNOP FM-12). Height: standard 10 m anemometer
(WMO), airport exposure (open terrain, z0 ~ 0.03 m).

Outputs (results/cfd/wind/): wind_hourly_clean.csv, rose_*.png, wind_stats.json,
cfd_scenarios.json (directions/speeds actually observed, with frequencies)
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data/meteo/isd_60490"
OUT = ROOT / "results/cfd/wind"
SECT = 16
NAMES = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
BINS = [0, 2, 4, 6, 8, 10, 14, 40]


def load():
    fr = []
    for f in sorted(SRC.glob("*.csv")):
        d = pd.read_csv(f, usecols=["DATE", "REPORT_TYPE", "WND", "TMP"], dtype=str)
        fr.append(d)
    d = pd.concat(fr, ignore_index=True)
    w = d["WND"].str.split(",", expand=True)
    d["dir"] = pd.to_numeric(w[0], errors="coerce"); d["dq"] = w[1]; d["typ"] = w[2]
    d["spd"] = pd.to_numeric(w[3], errors="coerce") / 10.0; d["sq"] = w[4]
    t = d["TMP"].str.split(",", expand=True)
    d["tair"] = pd.to_numeric(t[0], errors="coerce") / 10.0
    d.loc[(d["tair"] > 60) | (~t[1].isin(list("014569"))), "tair"] = np.nan
    d["time"] = pd.to_datetime(d["DATE"], utc=True)
    good = d["sq"].isin(list("01459")) & (d["spd"] < 90)
    d = d[good].copy()
    calm = (d["typ"] == "C") | (d["spd"] == 0)
    d.loc[calm, "dir"] = np.nan
    d = d[calm | (d["dir"].between(1, 360) & d["dq"].isin(list("01459")))]
    d["prio"] = d["REPORT_TYPE"].map({"FM-15": 0, "FM-12": 1}).fillna(2)
    d["hour"] = d["time"].dt.floor("h")
    d = d.sort_values(["hour", "prio"]).drop_duplicates("hour")
    d["calm"] = d["dir"].isna()
    return d[["hour", "REPORT_TYPE", "dir", "spd", "calm", "tair"]].reset_index(drop=True)


def sector(dirs):
    return (np.floor((np.asarray(dirs) % 360 + 360 / SECT / 2) / (360 / SECT)) % SECT).astype(int)


def rose(ax, d, title):
    th = np.radians(np.arange(SECT) * 360 / SECT)
    nc = d[~d["calm"]]
    sec = sector(nc["dir"])
    bottom = np.zeros(SECT)
    cols = plt.cm.viridis(np.linspace(0.1, 0.95, len(BINS) - 1))
    for k in range(len(BINS) - 1):
        m = (nc["spd"] >= BINS[k]) & (nc["spd"] < BINS[k + 1])
        f = np.bincount(sec[m.values], minlength=SECT) / len(d) * 100
        ax.bar(th, f, width=2 * np.pi / SECT * 0.92, bottom=bottom, color=cols[k],
               label=f"{BINS[k]}–{BINS[k + 1]} m/s", edgecolor="white", lw=0.3)
        bottom += f
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    ax.set_xticks(th); ax.set_xticklabels(NAMES, fontsize=7)
    ax.set_title(f"{title}\n(calmes {100 * d['calm'].mean():.1f} %, n = {len(d)} h)", fontsize=9)
    return bottom


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d = load()
    d.to_csv(OUT / "wind_hourly_clean.csv", index=False)
    d["month"] = d["hour"].dt.month
    d["hloc"] = (d["hour"].dt.hour + 1) % 24                      # Algeria = UTC+1, no DST
    seasons = {"Hiver (DJF)": [12, 1, 2], "Printemps (MAM)": [3, 4, 5], "Été (JJA)": [6, 7, 8],
               "Automne (SON)": [9, 10, 11]}
    st = {"source": "NOAA ISD 60490099999 ES SENIA (35.624N 0.621W, 90 m)", "period": [str(d["hour"].min()), str(d["hour"].max())],
          "hours": int(len(d)), "calm_pct": float(100 * d["calm"].mean()), "mean_speed_ms": float(d["spd"].mean()),
          "p50": float(d["spd"].quantile(.5)), "p90": float(d["spd"].quantile(.9)), "p99": float(d["spd"].quantile(.99)),
          "max": float(d["spd"].max())}
    k, loc, c = stats.weibull_min.fit(d.loc[d["spd"] > 0, "spd"], floc=0)
    st["weibull_k"], st["weibull_c_ms"] = float(k), float(c)
    nc = d[~d["calm"]]
    sec = sector(nc["dir"])
    freq = np.bincount(sec, minlength=SECT) / len(d) * 100
    mean_by = np.array([nc["spd"].values[sec == s].mean() if (sec == s).any() else 0 for s in range(SECT)])
    p90_by = np.array([np.percentile(nc["spd"].values[sec == s], 90) if (sec == s).sum() > 20 else 0 for s in range(SECT)])
    st["sectors"] = [{"secteur": NAMES[s], "dir_deg": s * 22.5, "freq_pct": float(freq[s]), "v_moy": float(mean_by[s]),
                      "v_p90": float(p90_by[s])} for s in range(SECT)]
    st["seasons"] = {}
    for n, ms in seasons.items():
        ds = d[d["month"].isin(ms)]; s2 = sector(ds.loc[~ds["calm"], "dir"])
        f2 = np.bincount(s2, minlength=SECT) / len(ds) * 100
        top = np.argsort(f2)[::-1][:3]
        st["seasons"][n] = {"v_moy": float(ds["spd"].mean()), "calm_pct": float(100 * ds["calm"].mean()),
                            "top3": [(NAMES[t], float(f2[t])) for t in top],
                            "tair_moy": float(ds["tair"].mean())}
    for n, hs in {"jour (10-17 h locales)": range(10, 18), "nuit (22-5 h locales)": [22, 23, 0, 1, 2, 3, 4, 5]}.items():
        ds = d[d["hloc"].isin(hs)]; s2 = sector(ds.loc[~ds["calm"], "dir"])
        f2 = np.bincount(s2, minlength=SECT) / len(ds) * 100
        top = np.argsort(f2)[::-1][:3]
        st[n] = {"v_moy": float(ds["spd"].mean()), "top3": [(NAMES[t], float(f2[t])) for t in top]}
    # summer afternoons = heat-island situation (the thermal maps are mid-July, ~10:50 UTC)
    sa = d[d["month"].isin([6, 7, 8]) & d["hloc"].between(11, 17)]
    s3 = sector(sa.loc[~sa["calm"], "dir"]); f3 = np.bincount(s3, minlength=SECT) / len(sa) * 100
    st["ete_apres_midi"] = {"v_moy": float(sa["spd"].mean()), "tair_moy": float(sa["tair"].mean()),
                            "top3": [(NAMES[t], float(f3[t])) for t in np.argsort(f3)[::-1][:3]]}
    # ---- figures
    fig = plt.figure(figsize=(14, 10), dpi=130)
    ax = fig.add_subplot(2, 3, 1, projection="polar"); rose(ax, d, "Annuel 2014–2024")
    ax.legend(loc="lower left", bbox_to_anchor=(-0.35, -0.25), fontsize=7)
    for i, (n, ms) in enumerate(seasons.items()):
        rose(fig.add_subplot(2, 3, i + 2, projection="polar"), d[d["month"].isin(ms)], n)
    rose(fig.add_subplot(2, 3, 6, projection="polar"), sa, "Été, 11–17 h locales")
    fig.suptitle("Oran – Es Sénia (NOAA ISD 60490), vent à 10 m — fréquences en % des heures", fontsize=12)
    fig.tight_layout(); fig.savefig(OUT / "rose_des_vents_oran_2014_2024.png"); plt.close(fig)
    fig, ax = plt.subplots(1, 2, figsize=(12, 4), dpi=130)
    v = d["spd"].values; xs = np.linspace(0, 20, 200)
    ax[0].hist(v, bins=np.arange(0, 21, 1), density=True, color="#90a4ae")
    ax[0].plot(xs, stats.weibull_min.pdf(xs, k, 0, c), "r-", label=f"Weibull k={k:.2f}, c={c:.2f} m/s")
    ax[0].set_xlabel("vitesse à 10 m (m/s)"); ax[0].legend(); ax[0].set_title("Distribution des vitesses")
    hm = d.groupby("hloc")["spd"].mean(); ax[1].plot(hm.index, hm.values, "o-")
    ax[1].set_xlabel("heure locale"); ax[1].set_ylabel("vitesse moyenne (m/s)"); ax[1].set_title("Cycle journalier")
    fig.tight_layout(); fig.savefig(OUT / "distribution_vitesses.png"); plt.close(fig)
    # ---- CFD scenarios from the observed climate (not assumed)
    order = np.argsort(freq)[::-1]
    sc = []
    for s in order[:4]:
        sc.append({"id": f"S{len(sc) + 1}_{NAMES[s]}", "dir_from_deg": float(s * 22.5), "freq_pct": float(freq[s]),
                   "U10_ref_ms": round(float(mean_by[s]), 2), "role": "direction dominante observée, vitesse moyenne du secteur"})
    s = order[0]
    sc.append({"id": f"S{len(sc) + 1}_{NAMES[s]}_fort", "dir_from_deg": float(s * 22.5), "freq_pct": float(freq[s]),
               "U10_ref_ms": round(float(p90_by[s]), 2), "role": "même direction, vent fort (P90 du secteur) : confort/sécurité"})
    ts = NAMES.index(st["ete_apres_midi"]["top3"][0][0])
    sc.append({"id": f"S{len(sc) + 1}_ete_{NAMES[ts]}", "dir_from_deg": float(ts * 22.5),
               "U10_ref_ms": round(st["ete_apres_midi"]["v_moy"], 2), "Tair_ref_C": round(st["ete_apres_midi"]["tair_moy"], 1),
               "role": "après-midi d'été (situation des cartes LST) : cas couplé thermique"})
    st["cfd_scenarios"] = sc
    (OUT / "wind_stats.json").write_text(json.dumps(st, indent=1, ensure_ascii=False))
    (OUT / "cfd_scenarios.json").write_text(json.dumps(sc, indent=1, ensure_ascii=False))
    print(json.dumps({k_: st[k_] for k_ in ("hours", "calm_pct", "mean_speed_ms", "p90", "weibull_k", "weibull_c_ms",
                                             "seasons", "ete_apres_midi", "cfd_scenarios")}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
