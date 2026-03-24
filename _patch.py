import pathlib
p = pathlib.Path(r"c:\Users\Seboss\lycee-suggestions\templates\admin.html")
c = p.read_text(encoding="utf-8")

# Fix context hint (may have curly quote)
for variant in [
    "Liste en cours, journal d\u2019activit\u00e9, et copie compl\u00e8te des fiches (y compris supprim\u00e9es).",
    "Liste en cours, journal d'activit\u00e9, et copie compl\u00e8te des fiches (y compris supprim\u00e9es).",
]:
    old = f'<span class="context-hint">{variant}</span>'
    if old in c:
        c = c.replace(old, '<span class="context-hint">Consultez, modifiez et suivez les suggestions des \u00e9l\u00e8ves.</span>')
        print("Fixed context hint")
        break

# Tab renames
renames = [
    ("Liste &amp; \u00e9dition", "Suggestions"),
    ("Logs en direct", "Journal"),
    ("Historique &amp; export", "Archives"),
]
for old, new in renames:
    if old in c:
        c = c.replace(old, new, 1)
        print(f"Tab: {old} -> {new}")

# Replace filters section
old_filters = '''<div class="section-filters">
                    <select id="admin-filter-category"><option value="">Toutes les cat\u00e9gories</option></select>
                    <select id="admin-filter-status"><option value="">Tous les statuts</option></select>
                    <input type="search" id="admin-search" placeholder="Rechercher...">
                </div>'''
new_filters = '''<div class="asg-status-bar" id="asg-status-bar">
                    <button class="asg-status-tab asg-status-tab--active" data-asg-status="accepted">Accept\u00e9es</button>
                    <button class="asg-status-tab" data-asg-status="pending">En attente</button>
                    <button class="asg-status-tab" data-asg-status="refused">Refus\u00e9es</button>
                    <button class="asg-status-tab" data-asg-status="all">Tout voir</button>
                </div>
                <div class="asg-filter-bar">
                    <select id="admin-filter-category"><option value="">Toutes cat\u00e9gories</option></select>
                    <input type="search" id="admin-search" placeholder="Rechercher une suggestion...">
                    <span class="asg-count" id="asg-count"></span>
                </div>
                <select id="admin-filter-status" hidden><option value="">Tous les statuts</option></select>'''
if old_filters in c:
    c = c.replace(old_filters, new_filters)
    print("Replaced filters")
else:
    print("SKIP filters - not found")

# Clean up extra whitespace
c = c.replace("                \n                <div id=\"suggestion-focus-panel\"", "                <div id=\"suggestion-focus-panel\"")

p.write_text(c, encoding="utf-8")
print("Done!")
