#!/usr/bin/env python3
"""
create_excel_template.py  —  v2
DBA Operations Uptime Tracker — Professional Excel Workbook
────────────────────────────────────────────────────────────
What's new in v2
  • Dashboard summary sheet with KPI cards and live-link formulas
  • All 7 data sheets use the exact view column aliases
  • Replication sheet now has 'Health Status' and 'Stored Status' columns
  • Server Health has 'Health Status' and 'Stored Status' columns
  • Availability sheet has both Stored % and Computed % plus SLA Status
  • Boolean columns shown as Yes/No
  • Conditional formatting on every status/severity/health column
  • Alternating row shading for readability
  • Properly sized columns — no more tiny or overflowing cells
  • Auto-filter on every sheet
  • Freeze top row on every sheet

Run:
  python create_excel_template.py
  (or:  .\\venv\\Scripts\\python.exe create_excel_template.py)

Requires: openpyxl   →   pip install openpyxl
"""

from openpyxl import Workbook
from datetime import datetime
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side,
    GradientFill
)
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import (
    ColorScaleRule, CellIsRule, FormulaRule
)
from openpyxl.styles.differential import DifferentialStyle

# ── Colour Palette ────────────────────────────────────────────────────────────
C = dict(
    navy       = '002060',   # header background (Deep Navy)
    navy_light = '002060',   # sub-header / accent
    white      = 'FFFFFF',
    off_white  = 'F5F7FA',   # alternating row A
    row_b      = 'EBF0F7',   # alternating row B
    grey_border= 'C5CDD8',
    # Status colours (from original CF report)
    green      = 'FF006100',   # Dark green text
    green_light= 'FFC6EFCE',   # Light green fill
    amber      = 'FF9C6500',   # Dark amber text
    amber_light= 'FFFFEB9C',   # Light amber fill
    red        = 'FF9C0006',   # Dark red text
    red_light  = 'FFFFC7CE',   # Light red fill
    blue       = '2471A3',
    blue_light = 'D6EAF8',
    grey       = '7F8C8D',
    grey_light = 'F2F3F4',
    # KPI card
    kpi_bg     = '1B3A6B',
    kpi_text   = 'FFFFFF',
)


def _fill(hex_color):
    return PatternFill('solid', fgColor=hex_color)


FONT = 'Bookman Old Style'

def _font(bold=False, italic=False, color='000000', size=10, name=None):
    return Font(bold=bold, italic=italic, color=color, size=size, name=name or FONT)


def _border(style='thin', color=None):
    color = color or C['grey_border']
    s = Side(style=style, color=color)
    return Border(left=s, right=s, top=s, bottom=s)


def _align(h='left', v='center', wrap=False):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)


# ── Sheet definitions ─────────────────────────────────────────────────────────
SHEETS = [
    {
        'name': 'Incident Register',
        'view': 'vw_incident_register',
        'tab_color': 'C0392B',   # red
        'columns': [
            ('Ticket',           15, None),
            ('Date',             14, 'date'),
            ('Start',            12, 'time'),
            ('End',              12, 'time'),
            ('Duration (hrs)',   14, 'num2'),
            ('Planned',          10, 'yn'),
            ('Severity',         12, 'severity'),
            ('System',           22, None),
            ('Root Cause',       36, 'wrap'),
            ('Business Impact',  36, 'wrap'),
            ('Resolved By',      18, None),
            ('Status',           14, 'inc_status'),
            ('Database',         18, None),
        ],
        'dropdowns': {
            'Severity': ['Low', 'Medium', 'High', 'Critical'],
            'Status':   ['Open', 'In Progress', 'Resolved', 'Closed'],
            'Planned':  ['Yes', 'No'],
        },
    },
    {
        'name': 'Replication Monitor',
        'view': 'vw_replication_monitor',
        'tab_color': 'E67E22',   # orange
        'columns': [
            ('Check Date',      14, 'date'),
            ('Master',          22, None),
            ('Replica',         22, None),
            ('IO Thread',       12, 'thread'),
            ('SQL Thread',      12, 'thread'),
            ('Seconds Behind',  14, 'num0'),
            ('Last Error',      38, 'wrap'),
            ('Health Status',   20, 'health'),
            ('Stored Status',   16, None),
            ('Database',        18, None),
            ('Severity',        12, 'severity'),
        ],
        'dropdowns': {
            'IO Thread':    ['Yes', 'No', 'Connecting'],
            'SQL Thread':   ['Yes', 'No', 'Connecting'],
            'Severity':     ['Low', 'Medium', 'High', 'Critical'],
        },
    },
    {
        'name': 'Backup & Recovery',
        'view': 'vw_backup_recovery',
        'tab_color': '27AE60',   # green
        'columns': [
            ('Record Type',      14, None),
            ('Database',         18, None),
            ('Backup Date',      14, 'date'),
            ('Instance',         22, None),
            ('Backup Type',      16, None),
            ('Tool Used',        16, None),
            ('Start Time',       12, 'time'),
            ('End Time',         12, 'time'),
            ('Duration (hrs)',   14, 'num2'),
            ('Size (GB)',        12, 'num2'),
            ('Outcome',          14, 'outcome'),
            ('Location',         30, 'wrap'),
            ('Verified',         10, 'yn'),
            ('Restore Tested',   14, 'yn'),
            ('Compression',      12, 'yn'),
            ('Retention',        14, None),
            ('Tested By',        16, None),
            ('Comments',         36, 'wrap'),
        ],
        'dropdowns': {
            'Record Type': ['Backup', 'Recovery'],
            'Backup Type': ['Full', 'Incremental', 'Differential'],
            'Outcome':     ['Success', 'Failed', 'Partial'],
            'Verified':    ['Yes', 'No'],
            'Restore Tested': ['Yes', 'No'],
            'Compression': ['Yes', 'No'],
        },
    },
    {
        'name': 'Server Health',
        'view': 'vw_server_health',
        'tab_color': '8E44AD',   # purple
        'columns': [
            ('Check Date',      14, 'date'),
            ('Server',          22, None),
            ('CPU %',           10, 'pct'),
            ('Memory %',        10, 'pct'),
            ('Disk %',          10, 'pct'),
            ('Tablespace %',    13, 'pct'),
            ('Connections',     13, 'num0'),
            ('Slow Queries',    13, 'num0'),
            ('Threads',         10, 'num0'),
            ('Health Status',   16, 'health'),
            ('Stored Status',   14, None),
            ('Database',        18, None),
        ],
        'dropdowns': {
            'Health Status': ['Healthy', 'Warning', 'Critical'],
        },
    },
    {
        'name': 'Maintenance Register',
        'view': 'vw_maintenance_register',
        'tab_color': '2980B9',   # blue
        'columns': [
            ('Date',             16, 'date'),
            ('Change ID',        14, None),
            ('Activity',         40, 'wrap'),
            ('Start',            12, 'time'),
            ('End',              12, 'time'),
            ('Duration (hrs)',   14, 'num2'),
            ('Risk',             10, 'risk'),
            ('Rollback Plan',    36, 'wrap'),
            ('Approved By',      18, None),
            ('Success',          10, 'yn'),
            ('Database',         18, None),
        ],
        'dropdowns': {
            'Risk':    ['Low', 'Medium', 'High'],
            'Success': ['Yes', 'No'],
        },
    },
    {
        'name': 'Capacity Planning',
        'view': 'vw_capacity_planning',
        'tab_color': '16A085',   # teal
        'columns': [
            ('Month-Year',           14, None),
            ('Database Size (GB)',   18, 'num2'),
            ('Disk Used %',          13, 'pct'),
            ('Tablespace Used %',    16, 'pct'),
            ('Largest Table',        22, None),
            ('Growth (GB)',          14, 'num2'),
            ('Database',             18, None),
        ],
        'dropdowns': {},
    },
    {
        'name': 'Availability Calculator',
        'view': 'vw_availability_calculator',
        'tab_color': 'D35400',   # burnt orange
        'columns': [
            ('Month-Year',               14, None),
            ('Database Type',            16, None),
            ('Database',                 18, None),
            ('Target Hours',             14, 'num2'),
            ('Hours Achieved',           16, 'num2'),
            ('Planned Downtime (hrs)',   20, 'num2'),
            ('Unplanned Downtime (hrs)', 22, 'num2'),
            ('Availability % (Stored)',  20, 'avail_pct'),
            ('Availability % (Computed)',22, 'avail_pct'),
            ('SLA %',                    10, 'num2'),
            ('SLA Status',               12, 'sla_status'),
        ],
        'dropdowns': {
            'Database Type': ['ORACLE', 'MYSQL', 'MS SQL SERVER'],
            'SLA Status':    ['Met', 'Missed'],
        },
    },
]


# ── Conditional formatting helpers ───────────────────────────────────────────
def apply_conditional_formatting(ws, col_letter, col_type, max_row=500):
    rng = f'{col_letter}2:{col_letter}{max_row}'

    def cf_text(val, bg_color, fg_color):
        ws.conditional_formatting.add(
            rng,
            CellIsRule(operator='equal', formula=[f'"{val}"'], fill=_fill(bg_color), font=_font(color=fg_color, bold=True))
        )

    if col_type == 'severity':
        cf_text('Critical', C['red'],         C['white'])
        cf_text('High',     C['amber_light'],  C['amber'])
        cf_text('Medium',   C['blue_light'],   C['blue'])
        cf_text('Low',      C['green_light'],  C['green'])

    elif col_type == 'inc_status':
        cf_text('Open',        C['red_light'],   C['red'])
        cf_text('In Progress', C['amber_light'],  C['amber'])
        cf_text('Resolved',    C['green_light'],  C['green'])
        cf_text('Closed',      C['grey_light'],   C['grey'])

    elif col_type == 'health':
        cf_text('Healthy',              C['green_light'],  C['green'])
        cf_text('OK (<10 s)',           C['green_light'],  C['green'])
        cf_text('Warning',              C['amber_light'],  C['amber'])
        cf_text('Degraded (<1 min)',    C['amber_light'],  C['amber'])
        cf_text('Connecting',           C['blue_light'],   C['blue'])
        cf_text('Critical',             C['red'],          C['white'])
        cf_text('Critical Lag',         C['red'],          C['white'])
        cf_text('Failed – IO Thread Down',  C['red'],      C['white'])
        cf_text('Failed – SQL Thread Down', C['red'],      C['white'])

    elif col_type == 'outcome':
        cf_text('Success', C['green_light'],  C['green'])
        cf_text('Partial', C['amber_light'],  C['amber'])
        cf_text('Failed',  C['red'],          C['white'])

    elif col_type == 'risk':
        cf_text('High',   C['red_light'],   C['red'])
        cf_text('Medium', C['amber_light'],  C['amber'])
        cf_text('Low',    C['green_light'],  C['green'])

    elif col_type == 'thread':
        cf_text('Yes',        C['green_light'], C['green'])
        cf_text('Connecting', C['blue_light'],   C['blue'])
        cf_text('No',         C['red'],          C['white'])

    elif col_type == 'yn':
        cf_text('Yes', C['green_light'], C['green'])
        cf_text('No',  C['red_light'],   C['red'])

    elif col_type == 'sla_status':
        cf_text('Met',    C['green_light'], C['green'])
        cf_text('Missed', C['red'],         C['white'])

    elif col_type == 'pct':
        ws.conditional_formatting.add(
            rng,
            ColorScaleRule(
                start_type='num', start_value=0,   start_color=C['green_light'],
                mid_type='num',   mid_value=70,    mid_color='FDEBD0',
                end_type='num',   end_value=100,   end_color=C['red'],
            )
        )

    elif col_type == 'avail_pct':
        ws.conditional_formatting.add(
            rng,
            ColorScaleRule(
                start_type='num', start_value=90,  start_color=C['red'],
                mid_type='num',   mid_value=99,    mid_color='FDEBD0',
                end_type='num',   end_value=100,   end_color=C['green_light'],
            )
        )



# ── Dashboard sheet ──────────────────────────────────────────────────────────
def create_dashboard(wb):
    ws = wb.create_sheet("Dashboard", 0)
    ws.sheet_properties.tabColor = C['navy']
    ws.sheet_view.showGridLines = False

    def hdr(cell_ref, value, bg=C['navy'], fg=C['white'], bold=True, size=9, h_align='center'):
        c = ws[cell_ref]
        c.value = value
        c.font = _font(bold=bold, color=fg, size=size)
        c.fill = _fill(bg)
        c.alignment = _align(h_align, 'center', wrap=True)
        c.border = _border()
        return c

    def kpi(cell_ref, formula, num_fmt='0.00', bg=C['off_white'], fg='000000'):
        c = ws[cell_ref]
        c.value = formula
        c.font = _font(bold=False, size=10, color=fg)
        c.fill = _fill(bg)
        c.alignment = _align('center', 'center')
        c.number_format = num_fmt
        c.border = _border()
        return c

    col_widths = {'A': 18, 'B': 14, 'C': 12, 'D': 12, 'E': 12, 'F': 12,
                  'G': 12, 'H': 12, 'I': 14, 'J': 14, 'K': 14, 'L': 14,
                  'M': 12, 'N': 12, 'O': 12}
    for col, w in col_widths.items():
        ws.column_dimensions[col].width = w

    # Row 1
    ws.merge_cells('A1:O1')
    t = ws['A1']
    t.value = '="DBA Operations Dashboard " & TEXT(TODAY(), "mmmm yyyy")'
    t.font = _font(bold=True, size=14, color=C['white'], name='Bookman Old Style')
    t.fill = _fill(C['navy'])
    t.alignment = _align('center', 'center')
    ws.row_dimensions[1].height = 24

    # Row 2
    ws['A2'] = 'Select Database:'
    ws['A2'].font = _font(bold=True, size=11, name='Bookman Old Style')
    ws['A2'].alignment = _align('left', 'center')
    ws.row_dimensions[2].height = 20

    from openpyxl.worksheet.datavalidation import DataValidation
    dv = DataValidation(type='list', formula1='"All Databases,Oracle,MySQL,MS SQL Server"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add(ws['B2'])
    ws['B2'] = 'MySQL'
    ws['B2'].font = _font(bold=True, size=11, name='Bookman Old Style')
    ws['B2'].border = _border()
    ws['B2'].alignment = _align('left', 'center')

    # Row 3
    ws.merge_cells('A3:J3')
    ws['A3'] = 'Side-by-side comparison across all monitored databases, with detail for the selected database below.'
    ws['A3'].font = _font(italic=True, color=C['grey'], size=10, name='Bookman Old Style')
    ws['A3'].alignment = _align('left', 'center')
    ws.row_dimensions[3].height = 16

    # Row 5 Headers
    ws.row_dimensions[5].height = 40
    headers_top = [
        ('A', 'Database'), ('B', 'Overall\\nAvailability %'), ('C', 'Planned\\nDowntime\\n(hrs)'),
        ('D', 'Unplanned\\nDowntime\\n(hrs)'), ('E', 'Total\\nIncidents'), ('F', 'MTTR (mins)'),
        ('G', 'Backup\\nSuccess %'), ('H', 'Restore\\nSuccess %'), ('I', 'Replication\\nStatus'),
        ('J', 'Failed\\nBackups')
    ]
    for col, lbl in headers_top:
        hdr(f'{col}5', lbl)

    # Rows 6-8 Data
    dbs = [(6, 'Oracle'), (7, 'MySQL'), (8, 'MS SQL Server')]
    for r, db in dbs:
        ws[f'A{r}'] = db
        ws[f'A{r}'].font = _font(bold=False, name='Bookman Old Style')
        ws[f'A{r}'].border = _border()
        ws[f'A{r}'].alignment = _align('center', 'center')
        
        kpi(f'B{r}', f'=IFERROR(AVERAGEIFS(\'Availability Calculator\'!H:H,\'Availability Calculator\'!C:C,$A{r},\'Availability Calculator\'!A:A,TEXT(TODAY(),"YYYY-MM")),1)', '0.00%')
        kpi(f'C{r}', f'=SUMIFS(\'Maintenance Register\'!F:F,\'Maintenance Register\'!A:A,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Maintenance Register\'!K:K,$A{r})', '0.00')
        kpi(f'D{r}', f'=SUMIFS(\'Incident Register\'!E:E,\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Incident Register\'!F:F,"No",\'Incident Register\'!M:M,$A{r})', '0.00')
        kpi(f'E{r}', f'=COUNTIFS(\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Incident Register\'!M:M,$A{r})', '0')
        kpi(f'F{r}', f'=IFERROR(AVERAGEIFS(\'Incident Register\'!E:E,\'Incident Register\'!M:M,$A{r},\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1))*60,"No Data")', '0.00')
        kpi(f'G{r}', f'=IF(COUNTIFS(\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!B:B,$A{r},\'Backup & Recovery\'!C:C,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1))=0,"No Data",COUNTIFS(\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!K:K,"Success",\'Backup & Recovery\'!B:B,$A{r},\'Backup & Recovery\'!C:C,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1))/COUNTIFS(\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!B:B,$A{r},\'Backup & Recovery\'!C:C,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1)))', '0.00%')
        kpi(f'H{r}', f'=IF(COUNTIFS(\'Backup & Recovery\'!A:A,"Restore Test",\'Backup & Recovery\'!B:B,$A{r})=0,"No Data",COUNTIFS(\'Backup & Recovery\'!A:A,"Restore Test",\'Backup & Recovery\'!K:K,"Pass",\'Backup & Recovery\'!B:B,$A{r})/COUNTIFS(\'Backup & Recovery\'!A:A,"Restore Test",\'Backup & Recovery\'!B:B,$A{r}))', '0.00%')
        kpi(f'I{r}', f'=IF(COUNTIFS(\'Replication Monitor\'!J:J,$A{r})=0,"No Data",IF(COUNTIFS(\'Replication Monitor\'!J:J,$A{r},\'Replication Monitor\'!H:H,"Failed")>0,"Failed",IF(COUNTIFS(\'Replication Monitor\'!J:J,$A{r},\'Replication Monitor\'!H:H,"Degraded")>0,"Degraded","Healthy")))', '@')
        kpi(f'J{r}', f'=COUNTIFS(\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!K:K,"Failed",\'Backup & Recovery\'!B:B,$A{r},\'Backup & Recovery\'!C:C,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1))', '0')

    ws.row_dimensions[11].height = 20
    ws['A11'] = 'Database Detail'
    ws['A11'].font = _font(bold=True, size=12, name='Bookman Old Style')

    ws.row_dimensions[12].height = 16
    ws['A12'] = 'Showing: '
    ws['A12'].font = _font(bold=True, size=10, name='Bookman Old Style')
    ws['B12'] = '=$B$2'
    ws['B12'].font = _font(bold=True, size=10, name='Bookman Old Style')

    # Row 14 Headers
    ws.row_dimensions[14].height = 40
    headers_bot = [
        ('A', 'Metric'), ('B', 'Overall\\nAvailability'), ('C', 'Planned\\nDowntime'),
        ('D', 'Unplanned\\nDowntime'), ('E', 'Total\\nIncidents'), ('F', 'MTTR'),
        ('G', 'Backup\\nSuccess Rate'), ('H', 'Restore\\nSuccess Rate'), ('I', 'Replication\\nStatus'),
        ('J', 'Last\\nSuccessful\\nFull Backup'), ('K', 'Last\\nSuccessful\\nIncremental\\nBackup'),
        ('L', 'Last DR Test'), ('M', 'Failed\\nBackups'), ('N', 'Failed\\nRestore Tests'),
        ('O', 'Replication\\nLag')
    ]
    for col, lbl in headers_bot:
        hdr(f'{col}14', lbl)

    # Row 15: Current
    hdr('A15', 'Current')
    kpi('B15', '=_xlfn.IFS($B$2="All Databases",AVERAGE(B6:B8),$B$2="Oracle",B6,$B$2="MySQL",B7,$B$2="MS SQL Server",B8)', '0.00%')
    kpi('C15', '=_xlfn.IFS($B$2="All Databases",SUM(C6:C8),$B$2="Oracle",C6,$B$2="MySQL",C7,$B$2="MS SQL Server",C8)', '0.00')
    kpi('D15', '=_xlfn.IFS($B$2="All Databases",SUM(D6:D8),$B$2="Oracle",D6,$B$2="MySQL",D7,$B$2="MS SQL Server",D8)', '0.00')
    kpi('E15', '=_xlfn.IFS($B$2="All Databases",SUM(E6:E8),$B$2="Oracle",E6,$B$2="MySQL",E7,$B$2="MS SQL Server",E8)', '0')
    kpi('F15', '=_xlfn.IFS($B$2="All Databases",AVERAGE(F6:F8),$B$2="Oracle",F6,$B$2="MySQL",F7,$B$2="MS SQL Server",F8)', '0.00')
    kpi('G15', '=_xlfn.IFS($B$2="All Databases",AVERAGE(G6:G8),$B$2="Oracle",G6,$B$2="MySQL",G7,$B$2="MS SQL Server",G8)', '0.00%')
    kpi('H15', '=_xlfn.IFS($B$2="All Databases",AVERAGE(H6:H8),$B$2="Oracle",H6,$B$2="MySQL",H7,$B$2="MS SQL Server",H8)', '0.00%')
    kpi('I15', '=IF($B$2="All Databases",IF(COUNTIF(I6:I8,"Failed")>0,"Failed",IF(COUNTIF(I6:I8,"Degraded")>0,"Degraded","Healthy")),_xlfn.IFS($B$2="Oracle",I6,$B$2="MySQL",I7,$B$2="MS SQL Server",I8))', '@')
    kpi('J15', '=IF($B$2="All Databases",_xlfn.MAXIFS(\'Backup & Recovery\'!C:C,\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!E:E,"Full",\'Backup & Recovery\'!K:K,"Success"),_xlfn.MAXIFS(\'Backup & Recovery\'!C:C,\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!E:E,"Full",\'Backup & Recovery\'!K:K,"Success",\'Backup & Recovery\'!B:B,$B$2))', 'M/D/YYYY')
    kpi('K15', '=IF($B$2="All Databases",_xlfn.MAXIFS(\'Backup & Recovery\'!C:C,\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!E:E,"Incremental",\'Backup & Recovery\'!K:K,"Success"),_xlfn.MAXIFS(\'Backup & Recovery\'!C:C,\'Backup & Recovery\'!A:A,"Backup",\'Backup & Recovery\'!E:E,"Incremental",\'Backup & Recovery\'!K:K,"Success",\'Backup & Recovery\'!B:B,$B$2))', 'M/D/YYYY')
    kpi('L15', '=IF($B$2="All Databases",_xlfn.MAXIFS(\'Backup & Recovery\'!C:C,\'Backup & Recovery\'!A:A,"Restore Test",\'Backup & Recovery\'!K:K,"Pass"),_xlfn.MAXIFS(\'Backup & Recovery\'!C:C,\'Backup & Recovery\'!A:A,"Restore Test",\'Backup & Recovery\'!K:K,"Pass",\'Backup & Recovery\'!B:B,$B$2))', 'M/D/YYYY')
    kpi('M15', '=_xlfn.IFS($B$2="All Databases",SUM(J6:J8),$B$2="Oracle",J6,$B$2="MySQL",J7,$B$2="MS SQL Server",J8)', '0')
    kpi('N15', '=IF($B$2="All Databases",COUNTIFS(\'Backup & Recovery\'!A:A,"Restore Test",\'Backup & Recovery\'!K:K,"Fail"),COUNTIFS(\'Backup & Recovery\'!A:A,"Restore Test",\'Backup & Recovery\'!K:K,"Fail",\'Backup & Recovery\'!B:B,$B$2))', '0')
    kpi('O15', '=IF($B$2="All Databases",MAX(\'Replication Monitor\'!F:F),_xlfn.MAXIFS(\'Replication Monitor\'!F:F,\'Replication Monitor\'!J:J,$B$2))', '0')

    # Row 16: Target
    hdr('A16', 'Target')
    targets = [('B','>= 99.00%'), ('C','<= 48 hrs'), ('D','<= 2 hrs'), ('E','<= 4/month'),
               ('F','<= 4 hrs'), ('G','>= 95%'), ('H','>= 80%'), ('I','Healthy'),
               ('J','< 7 days'), ('K','< 24 hrs'), ('L','< 6 months'), ('M','0'), ('N','0'), ('O','< 30 seconds')]
    for c, v in targets:
        kpi(f'{c}16', v, '@')

    # Row 17: Status
    hdr('A17', 'Status')
    kpi('B17', '=IF(B15>=0.99,"Meets SLA","Below SLA")','@')
    kpi('C17', '=IF(C15<=48,"Meets SLA","Below SLA")','@')
    kpi('D17', '=IF(D15<=2,"Meets SLA","Below SLA")','@')
    kpi('E17', '=IF(E15<=4,"Meets SLA","Below SLA")','@')
    kpi('F17', '=IF(F15<=240,"Meets SLA","Below SLA")','@')
    kpi('G17', '=IF(G15>=0.95,"Meets SLA","Below SLA")','@')
    kpi('H17', '=IF(H15>=0.80,"Meets SLA","Below SLA")','@')
    kpi('I17', '=IF(I15="Healthy","Meets SLA","Below SLA")','@')
    kpi('J17', '=IF(TODAY()-J15<=7,"Meets SLA","Below SLA")','@')
    kpi('K17', '=IF(TODAY()-K15<=1,"Meets SLA","Below SLA")','@')
    kpi('L17', '=IF(TODAY()-L15<=180,"Meets SLA","Below SLA")','@')
    kpi('M17', '=IF(M15=0,"Meets SLA","Below SLA")','@')
    kpi('N17', '=IF(N15=0,"Meets SLA","Below SLA")','@')
    kpi('O17', '=IF(O15<30,"Meets SLA","Below SLA")','@')

    # Incident Status Breakdown
    ws.row_dimensions[20].height = 20
    ws['A20'] = 'Incident Status Breakdown'
    ws['A20'].font = _font(bold=True, size=12, name='Bookman Old Style')

    ws.row_dimensions[21].height = 16
    ws['A21'] = '="This month, "&$B$2'
    ws['A21'].font = _font(italic=True, size=10, name='Bookman Old Style')

    inc_headers = ['Database', 'Open', 'In Progress', 'Resolved', 'Closed', 'On Hold', 'Total']
    for idx, header in enumerate(inc_headers):
        col = chr(ord('A') + idx)
        hdr(f'{col}23', header)
    
    kpi('A24', '=$B$2', '@')
    kpi('B24', '=COUNTIFS(\'Incident Register\'!L:L,"Open",\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Incident Register\'!M:M,$B$2)', '0')
    kpi('C24', '=COUNTIFS(\'Incident Register\'!L:L,"In Progress",\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Incident Register\'!M:M,$B$2)', '0.00')
    kpi('D24', '=COUNTIFS(\'Incident Register\'!L:L,"Resolved",\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Incident Register\'!M:M,$B$2)', '0')
    kpi('E24', '=COUNTIFS(\'Incident Register\'!L:L,"Closed",\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Incident Register\'!M:M,$B$2)', '0')
    kpi('F24', '=COUNTIFS(\'Incident Register\'!L:L,"On Hold",\'Incident Register\'!B:B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),\'Incident Register\'!M:M,$B$2)', '0.00')
    kpi('G24', '=SUM(B24:F24)', '0')

    # Footer
    ws['A27'] = 'Last Updated'
    ws['A27'].font = _font(italic=True, name='Bookman Old Style')
    ws['C27'] = '=NOW()'
    ws['C27'].number_format = 'M/D/YYYY HH:MM'
    ws['C27'].font = _font(italic=True, name='Bookman Old Style')

    # Key
    ws['A30'] = 'Key'
    ws['A30'].font = _font(bold=True, name='Bookman Old Style')
    
    ws['A31'] = 'Color'
    ws['A31'].font = _font(bold=True, name='Bookman Old Style')
    ws['A31'].border = _border()
    ws['B31'] = 'Grading Scale'
    ws['B31'].font = _font(bold=True, name='Bookman Old Style')
    ws['B31'].border = _border()

    ws['A32'].fill = _fill(C['amber_light'])
    ws['A32'].border = _border()
    ws['B32'] = 'Marginal\\nPerformance'
    ws['B32'].font = _font(name='Bookman Old Style')
    ws['B32'].border = _border()
    ws['B32'].alignment = _align('left', 'center', True)
    
    ws['A33'].fill = _fill(C['green_light'])
    ws['A33'].border = _border()
    ws['B33'] = 'Meets SLA\\nPerformance'
    ws['B33'].font = _font(name='Bookman Old Style')
    ws['B33'].border = _border()
    ws['B33'].alignment = _align('left', 'center', True)

    # Conditional Formatting for Dashboard
    from openpyxl.formatting.rule import CellIsRule, FormulaRule
    def cf(rng, val, bg, op='equal'):
        ws.conditional_formatting.add(rng, CellIsRule(operator=op, formula=[f'"{val}"'], fill=_fill(bg)))

    def cf_num(rng, val, bg, op='greaterThanOrEqual'):
        ws.conditional_formatting.add(rng, CellIsRule(operator=op, formula=[f'{val}'], fill=_fill(bg)))

    # Availability
    cf_num('B6:B8', '0.99', C['green_light'])
    cf_num('B6:B8', '0.97', C['amber_light'], 'between')
    ws.conditional_formatting.add('B6:B8', CellIsRule(operator='between', formula=['0.97', '0.9899'], fill=_fill(C['amber_light'])))
    cf_num('B6:B8', '0.97', C['red_light'], 'lessThan')
    cf_num('B15', '0.99', C['green_light'])
    cf_num('B15', '0.97', C['red_light'], 'lessThan')

    # Status text
    for rng in ['I6:I8', 'I15', 'B17:O17']:
        cf(rng, 'Healthy', C['green_light'])
        cf(rng, 'Failed', C['red_light'])
        cf(rng, 'Degraded', C['amber_light'])
        cf(rng, 'Meets SLA', C['green_light'])
        cf(rng, 'Below SLA', C['red_light'])
        cf(rng, 'Marginal', C['amber_light'])

    # Others
    cf_num('M15:N15', '0', C['green_light'])
    cf_num('M15:N15', '1', C['red_light'], 'greaterThanOrEqual')
    cf_num('J6:J8', '0', C['green_light'])
    cf_num('J6:J8', '1', C['red_light'], 'greaterThanOrEqual')

    # Row 15 targets
    cf_num('C15', '48', C['green_light'], 'lessThanOrEqual')
    cf_num('C15', '48', C['red_light'], 'greaterThan')
    cf_num('D15', '2', C['green_light'], 'lessThanOrEqual')
    cf_num('D15', '2', C['red_light'], 'greaterThan')
    cf_num('E15', '4', C['green_light'], 'lessThanOrEqual')
    cf_num('E15', '4', C['red_light'], 'greaterThan')
    cf_num('F15', '240', C['green_light'], 'lessThanOrEqual')
    cf_num('F15', '240', C['red_light'], 'greaterThan')
    cf_num('G15', '0.95', C['green_light'], 'greaterThanOrEqual')
    cf_num('G15', '0.95', C['red_light'], 'lessThan')
    cf_num('H15', '0.80', C['green_light'], 'greaterThanOrEqual')
    cf_num('H15', '0.80', C['red_light'], 'lessThan')
    cf_num('O15', '30', C['green_light'], 'lessThan')
    cf_num('O15', '30', C['red_light'], 'greaterThanOrEqual')
    
    # Dates for J15, K15, L15 (since they are calculated as TODAY()-val)
    ws.conditional_formatting.add('J15', FormulaRule(formula=['TODAY()-J15<=7'], fill=_fill(C['green_light'])))
    ws.conditional_formatting.add('J15', FormulaRule(formula=['TODAY()-J15>7'], fill=_fill(C['red_light'])))
    ws.conditional_formatting.add('K15', FormulaRule(formula=['TODAY()-K15<=1'], fill=_fill(C['green_light'])))
    ws.conditional_formatting.add('K15', FormulaRule(formula=['TODAY()-K15>1'], fill=_fill(C['red_light'])))
    ws.conditional_formatting.add('L15', FormulaRule(formula=['TODAY()-L15<=180'], fill=_fill(C['green_light'])))
    ws.conditional_formatting.add('L15', FormulaRule(formula=['TODAY()-L15>180'], fill=_fill(C['red_light'])))

    # Incident Breakdown CF
    cf_num('B24:G24', '0', C['green_light'])
    cf_num('B24:G24', '1', C['red_light'], 'greaterThanOrEqual')



# ── Build a data sheet ────────────────────────────────────────────────────────
def create_data_sheet(wb, sheet_def):
    ws = wb.create_sheet(sheet_def['name'])
    if 'tab_color' in sheet_def:
        ws.sheet_properties.tabColor = sheet_def['tab_color']
    ws.sheet_view.showGridLines = False

    columns     = sheet_def['columns']   # list of (name, width, fmt_type)
    dropdowns   = sheet_def.get('dropdowns', {})
    view_name   = sheet_def.get('view', '')

    # ── Header row ──────────────────────────────────────────────────────────
    ws.row_dimensions[1].height = 32

    for idx, (col_name, col_width, col_fmt) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=idx, value=col_name)
        cell.font      = Font(bold=True, color=C['white'], size=10, name=FONT)
        cell.fill      = _fill(C['navy'])
        cell.alignment = _align('center', 'center', wrap=True)
        cell.border    = _border('thin', C['navy_light'])
        ws.column_dimensions[get_column_letter(idx)].width = col_width

    # View source note in the sheet header margin
    ws.sheet_properties.filterMode = False
    note_row = 0  # written after data rows

    # ── Pre-populate 50 sample/placeholder rows with formatting ───────────
    MAX_DATA = 500
    for row_idx in range(2, MAX_DATA + 2):
        is_alt = (row_idx % 2 == 0)
        row_fill = _fill(C['off_white'] if is_alt else C['row_b'])
        ws.row_dimensions[row_idx].height = 18

        for col_idx, (col_name, _, col_fmt) in enumerate(columns, 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.fill      = row_fill
            cell.border    = _border('thin', C['grey_border'])
            cell.alignment = _align(
                'center' if col_fmt in ('date','time','num0','num2','pct','yn') else 'left',
                'center',
                wrap=(col_fmt == 'wrap')
            )
            # Number formats
            if col_fmt == 'date':
                cell.number_format = 'YYYY-MM-DD'
            elif col_fmt == 'time':
                cell.number_format = 'HH:MM:SS'
            elif col_fmt == 'num2':
                cell.number_format = '#,##0.00'
            elif col_fmt == 'num0':
                cell.number_format = '#,##0'
            elif col_fmt == 'pct':
                cell.number_format = '0.00'

    # ── Auto-filter ─────────────────────────────────────────────────────────
    last_col = get_column_letter(len(columns))
    ws.auto_filter.ref = f'A1:{last_col}1'

    # ── Freeze header ────────────────────────────────────────────────────────
    ws.freeze_panes = 'A2'

    # ── Conditional formatting ───────────────────────────────────────────────
    for col_idx, (col_name, _, col_fmt) in enumerate(columns, 1):
        if col_fmt:
            col_letter = get_column_letter(col_idx)
            apply_conditional_formatting(ws, col_letter, col_fmt, MAX_DATA + 1)

    # ── Dropdowns ────────────────────────────────────────────────────────────
    col_names = [c[0] for c in columns]
    for col_name, values in dropdowns.items():
        if col_name in col_names:
            col_idx    = col_names.index(col_name) + 1
            col_letter = get_column_letter(col_idx)
            formula    = '"' + ','.join(values) + '"'
            dv = DataValidation(type='list', formula1=formula, allow_blank=True)
            dv.error       = f'Choose from: {", ".join(values)}'
            dv.errorTitle  = 'Invalid Entry'
            dv.prompt      = f'Select: {", ".join(values)}'
            dv.promptTitle = col_name
            dv.add(f'{col_letter}2:{col_letter}{MAX_DATA + 1}')
            ws.add_data_validation(dv)

    # ── Source note in cell A1 comment (view name) ────────────────────────
    # (openpyxl comment requires additional setup; skip for compat)
    return ws


# ── Charts Sheet ─────────────────────────────────────────────────────────────
def create_charts_sheet(wb):
    """Create a blank canvas sheet for user charts with matching deep navy styling."""
    ws = wb.create_sheet("Charts")
    ws.sheet_properties.tabColor = C['navy']
    
    # Title Row
    ws.merge_cells('A1:M1')
    cell = ws['A1']
    cell.value = "DBA Operations Dashboard Charts"
    cell.font = _font(bold=True, color=C['white'], size=14)
    cell.fill = _fill(C['navy'])
    cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30
    
    # Subtitle / Timestamp
    ws.merge_cells('A2:D2')
    ws['A2'] = "All charts auto-update from data sheets. Last Updated:"
    ws['A2'].font = _font(italic=True, color=C['grey'])
    
    ws['E2'] = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws['E2'].font = _font(bold=True, color=C['navy'])
    
    # Clean up gridlines for a clean canvas
    ws.sheet_view.showGridLines = False


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    wb = Workbook()
    # Remove default sheet
    if 'Sheet' in wb.sheetnames:
        wb.remove(wb['Sheet'])

    create_dashboard(wb)
    
    # Create Charts sheet next
    create_charts_sheet(wb)

    for sheet_def in SHEETS:
        ws = create_data_sheet(wb, sheet_def)
        print(f'  ✓  Sheet created: {sheet_def["name"]:20}  ({len(sheet_def["columns"])} columns)')

    # Save — use timestamped name so existing file with your data is preserved
    from datetime import datetime as _dt
    timestamp = _dt.now().strftime('%Y%m%d_%H%M%S')
    base = 'DBA Operations Uptime Tracker'
    out = f'{base}_{timestamp}.xlsx'
    wb.save(out)
    print(f'\n✅  Workbook saved → {out}')
    print(f'   (Your existing "{base}.xlsx" is untouched.)')
    print('\nNext steps:')
    print('  1. Run the SQL script on MySQL:')
    print('       mysql -h 192.168.35.38 -u root -p dba_tracker < create_views_and_permissions.sql')
    print('  2. Set up an ODBC DSN named "dba_tracker" using excel_user credentials')
    print('  3. In Excel: Data → Get Data → From ODBC → dba_tracker')
    print('     Load each vw_* view into its matching sheet')
    print('  4. Data → Refresh All  — KPI cards on Dashboard update automatically')


if __name__ == '__main__':
    main()
