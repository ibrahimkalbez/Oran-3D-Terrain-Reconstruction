"""Wind climatology at Oran - Es Senia (NOAA ISD 604900), 2014-2024.
Reads ISD-lite hourly files (data/wind/*.gz), produces wind roses and the
CFD scenarios for Belgaid. Local time = UTC+1 (Algeria, no DST)."""
import glob, gzip, json, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

rows = []
for f in sorted(glob.glob("data/wind/604900-99999-*.gz")):
    for line in gzip.open(f, "rt"):
        p = line.split()
        y, m, d, h = map(int, p[:4]); T = int(p[4]); wd = int(p[7]); ws = int(p[8])
        if wd == -9999 or ws == -9999: continue
        rows.append((y, m, d, (h + 1) % 24, T / 10 if T != -9999 else np.nan, wd, ws / 10))
a = np.array(rows, float)
mon, hr, T, wd, ws = a[:, 1], a[:, 3], a[:, 4], a[:, 5], a[:, 6]
calm = ws < 0.5
sect = (np.round(wd / 22.5) % 16).astype(int)
names = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
bins = [0, 2, 4, 6, 8, 10, 14, 40]

def rose(ax, sel, title):
    s = sel & ~calm; n = sel.sum()
    th = np.deg2rad(np.arange(16) * 22.5); bottom = np.zeros(16)
    cols = plt.cm.viridis(np.linspace(0, 1, len(bins) - 1))
    for i in range(len(bins) - 1):
        k = s & (ws >= bins[i]) & (ws < bins[i + 1])
        fr = np.bincount(sect[k], minlength=16) / n * 100
        ax.bar(th, fr, width=np.deg2rad(20), bottom=bottom, color=cols[i], edgecolor="k", lw=.2,
               label=f"{bins[i]}-{bins[i+1]} m/s")
        bottom += fr
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    ax.set_xticks(th); ax.set_xticklabels(names, fontsize=7)
    ax.set_title(f"{title}\n(calmes {calm[sel].mean()*100:.1f} %, n = {n})", fontsize=9)

summer = np.isin(mon, [6, 7, 8]); aft = (hr >= 11) & (hr <= 17)
winter = np.isin(mon, [12, 1, 2]); night = (hr <= 6) | (hr >= 21)
fig, axs = plt.subplots(2, 2, subplot_kw=dict(projection="polar"), figsize=(11, 10))
for ax, sel, t in zip(axs.flat, [np.ones_like(calm), summer, summer & aft, winter & night],
                      ["Annuel 2014-2024", "Été (JJA)", "Été, 11-17 h locales", "Hiver, nuit (21-6 h)"]):
    rose(ax, sel.astype(bool), t)
axs[0, 0].legend(loc="lower left", bbox_to_anchor=(-0.35, -0.2), fontsize=7)
fig.suptitle("Oran - Es Sénia (NOAA ISD 604900), vent à 10 m - fréquences en % des heures")
fig.savefig("results/wind_roses_es_senia.png", dpi=150); plt.close(fig)

def stats(sel):
    s = sel & ~calm
    fr = np.bincount(sect[s], minlength=16) / sel.sum() * 100
    um = np.array([ws[s & (sect == i)].mean() if (s & (sect == i)).any() else 0 for i in range(16)])
    return fr, um
out = {"n_hours": int(len(ws)), "U10_mean": float(ws.mean()), "calm_pct": float(calm.mean() * 100)}
fr_a, um_a = stats(np.ones_like(calm, bool)); fr_s, um_s = stats(summer & aft)
out["annual"] = {names[i]: [round(fr_a[i], 2), round(um_a[i], 2)] for i in range(16)}
out["summer_afternoon"] = {names[i]: [round(fr_s[i], 2), round(um_s[i], 2)] for i in range(16)}
out["summer_afternoon_T_mean"] = float(np.nanmean(T[summer & aft]))
# Scenarios: dominant summer-afternoon sea breeze and dominant land wind (annual SO/OSO)
iS = int(np.argmax(fr_s)); iL = int(np.argmax(np.where(np.isin(np.arange(16), [9, 10, 11, 12]), fr_a, 0)))
out["scenarios"] = {
    "S1_brise_mer": {"dir_from_deg": iS * 22.5, "U10": round(um_s[iS], 2), "freq_pct_summer_aft": round(fr_s[iS], 1)},
    "S2_vent_terre": {"dir_from_deg": iL * 22.5, "U10": round(um_a[iL], 2), "freq_pct_annual": round(fr_a[iL], 1)},
}
json.dump(out, open("results/wind_climatology.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps({k: out[k] for k in ["n_hours", "U10_mean", "calm_pct", "summer_afternoon_T_mean", "scenarios"]}, indent=1, ensure_ascii=False))
