import numpy as np
import matplotlib.pyplot as plt

# Kategorien
categories = [
    "Relatedness",
    "Power",
    "Order",
    "Mastery",
    "Honor",
    "Status",
    "Goal",
    "Freedom",
    "Acceptance",
    "Curiosity"
]

# Daten
data = {
    "Person1": {
        "Relatedness": 8,
        "Power": 4,
        "Order": 8,
        "Mastery": 7,
        "Honor": 7,
        "Status": 3,
        "Goal": 6,
        "Freedom": 5,
        "Acceptance": 8,
        "Curiosity": 6
    },

    "Person2": {
        "Relatedness": 8,
        "Power": 5,
        "Order": 6,
        "Mastery": 7,
        "Honor": 8,
        "Status": 5,
        "Goal": 8,
        "Freedom": 8,
        "Acceptance": 7,
        "Curiosity": 9
    },

    "Person3": {
        "Relatedness": 2,
        "Power": 3,
        "Order": 3,
        "Mastery": 6,
        "Honor": 3,
        "Status": 3,
        "Goal": 4,
        "Freedom": 9,
        "Acceptance": 6,
        "Curiosity": 9
    },

    "Person4": {
        "Relatedness": 7,
        "Power": 5,
        "Order": 8,
        "Mastery": 9,
        "Honor": 8,
        "Status": 1,
        "Goal": 10,
        "Freedom": 9,
        "Acceptance": 7,
        "Curiosity": 7
    },

    "Person5": {
        "Relatedness": 6,
        "Power": 2,
        "Order": 4,
        "Mastery": 6,
        "Honor": 3,
        "Status": 2,
        "Goal": 8,
        "Freedom": 10,
        "Acceptance": 5,
        "Curiosity": 9
    }
}

# ==========================================================
# Radar-Parameter
# ==========================================================

num_vars = len(categories)
angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
angles += angles[:1]

# ==========================================================
# CHART 1: Alle Personen
# ==========================================================

fig1, ax1 = plt.subplots(
    figsize=(10, 10),
    subplot_kw=dict(polar=True)
)

for person, values_dict in data.items():

    values = [values_dict[cat] for cat in categories]
    values += values[:1]

    ax1.plot(
        angles,
        values,
        linewidth=2,
        label=person
    )

    ax1.fill(
        angles,
        values,
        alpha=0.08
    )

ax1.set_xticks(angles[:-1])
ax1.set_xticklabels(categories)

ax1.set_ylim(0, 10)
ax1.set_yticks(range(1, 11))

ax1.set_title(
    "Moving Motivators - Personenvergleich",
    fontsize=16,
    pad=30
)

ax1.legend(
    loc="upper right",
    bbox_to_anchor=(1.25, 1.1)
)

plt.tight_layout()

# ==========================================================
# Statistik berechnen
# ==========================================================

mean_values = []
min_values = []
max_values = []

for category in categories:

    values = [
        person[category]
        for person in data.values()
    ]

    mean_values.append(np.mean(values))
    min_values.append(np.min(values))
    max_values.append(np.max(values))

# Kreis schließen
mean_plot = mean_values + mean_values[:1]
min_plot = min_values + min_values[:1]
max_plot = max_values + max_values[:1]

# ==========================================================
# CHART 2: Mean / Min / Max
# ==========================================================

fig2, ax2 = plt.subplots(
    figsize=(10, 10),
    subplot_kw=dict(polar=True)
)

# Max
ax2.plot(
    angles,
    max_plot,
    linewidth=2,
    linestyle=":",
    label="Maximum"
)

# Min
ax2.plot(
    angles,
    min_plot,
    linewidth=2,
    linestyle="--",
    label="Minimum"
)

# Mittelwert
ax2.plot(
    angles,
    mean_plot,
    linewidth=4,
    label="Mittelwert"
)

ax2.fill(
    angles,
    mean_plot,
    alpha=0.15
)

ax2.set_xticks(angles[:-1])
ax2.set_xticklabels(categories)

ax2.set_ylim(0, 10)
ax2.set_yticks(range(1, 11))

ax2.set_title(
    "Moving Motivators - Mittelwert, Minimum und Maximum",
    fontsize=16,
    pad=30
)

ax2.legend(
    loc="upper right",
    bbox_to_anchor=(1.25, 1.1)
)

plt.tight_layout()

# ==========================================================
# Ausgabe Statistik
# ==========================================================

print("\n=== MITTELWERTE ===")
for c, v in zip(categories, mean_values):
    print(f"{c:15}: {v:.2f}")

print("\n=== MINIMUM ===")
for c, v in zip(categories, min_values):
    print(f"{c:15}: {v}")

print("\n=== MAXIMUM ===")
for c, v in zip(categories, max_values):
    print(f"{c:15}: {v}")

plt.show()