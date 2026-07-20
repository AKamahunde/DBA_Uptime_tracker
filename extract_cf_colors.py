"""Extract exact CF fill colors from the user's original uptime look workbook."""
import openpyxl
import json

wb = openpyxl.load_workbook(
    r"e:\dba\CascadeProjects\windsurf-project\dba uptime look.xlsx",
    data_only=False
)

out = {}
for sheet_name in wb.sheetnames:
    ws = wb[sheet_name]
    cf_colors = []
    for cf_range, rules in ws.conditional_formatting._cf_rules.items():
        for rule in rules:
            entry = {
                'range': str(cf_range),
                'type': rule.type,
                'formula': str(getattr(rule, 'formula', '')),
                'fill_bg': None,
                'fill_fg': None,
                'font_color': None,
                'font_bold': None,
            }
            try:
                if rule.dxf and rule.dxf.fill:
                    entry['fill_bg'] = str(rule.dxf.fill.start_color.rgb) if rule.dxf.fill.start_color else None
                    entry['fill_fg'] = str(rule.dxf.fill.end_color.rgb) if rule.dxf.fill.end_color else None
            except Exception:
                pass
            try:
                if rule.dxf and rule.dxf.font:
                    entry['font_color'] = str(rule.dxf.font.color.rgb) if rule.dxf.font.color else None
                    entry['font_bold'] = rule.dxf.font.bold
            except Exception:
                pass
            cf_colors.append(entry)
    out[sheet_name] = cf_colors

with open(r"e:\dba\CascadeProjects\windsurf-project\cf_colors_report.json", 'w') as f:
    json.dump(out, f, indent=2)

print("Done! Report saved to cf_colors_report.json")
