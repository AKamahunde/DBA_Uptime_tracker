# Excel Dashboard Recreation Guide

When you connect Excel to MySQL via ODBC, you're importing the raw data tables. Your original Excel dashboard and charts won't automatically transfer because they were built on the Excel data structure. This guide shows you how to recreate your dashboard and charts using the imported MySQL data.

## Understanding the Process

**What happens with ODBC connection:**
- Excel connects to MySQL and imports raw data tables
- The data structure may differ slightly from your original Excel sheets
- Formulas, charts, and dashboards need to be recreated in Excel
- The good news: your data is now centralized in MySQL and can be updated automatically

## Step 1: Import Data via ODBC

1. **Connect to MySQL**:
   - Data → Get Data → From Other Sources → From ODBC
   - Select your `dba_tracker` DSN
   - Use the `excel_user` credentials (read-only access)

2. **Import Tables**:
   - Select the tables you need:
     - incident_register
     - replication_monitor
     - backup_recovery
     - server_health
     - maintenance_register
     - capacity_planning
     - availability_calculator
   - Load each table as a separate worksheet or data table

## Step 2: Create Data Model (Optional but Recommended)

1. **Power Pivot/Data Model**:
   - Data → Manage Data Model
   - Create relationships between tables if needed
   - This enables more powerful analysis and dashboards

2. **Create Measures**:
   - Use DAX formulas to create calculated measures
   - Example: Total Incidents = COUNTROWS(incident_register)
   - Example: Availability % = SUM(availability_calculator[availability_percent])

## Step 3: Recreate Dashboard Components

### Recreate Summary Tables

1. **Incident Summary**:
   - Create a pivot table from incident_register
   - Group by severity, status, or date
   - Add calculated fields for totals

2. **Replication Status**:
   - Pivot table from replication_monitor
   - Count by status (Running, Stopped, Error)
   - Add conditional formatting for status indicators

3. **Backup Summary**:
   - Pivot table from backup_recovery
   - Group by outcome (Success, Failed)
   - Calculate success rate percentage

### Recreate Charts

1. **Incident Trend Chart**:
   - Select incident_register data
   - Insert → Line Chart
   - X-axis: Date
   - Y-axis: Count of incidents
   - Group by severity if needed

2. **Availability Gauge/Chart**:
   - Use availability_calculator data
   - Create a gauge chart or bar chart
   - Show availability % vs SLA %
   - Add conditional formatting (green for >99%, yellow for 95-99%, red for <95%)

3. **Server Health Metrics**:
   - Create combo chart from server_health
   - CPU % and Memory % as lines
   - Disk % as bars
   - Add threshold lines for alerts

4. **Capacity Planning Chart**:
   - Use capacity_planning data
   - Create area chart showing growth over time
   - Add trend line for forecasting
   - Include disk and tablespace usage

### Recreate Dashboard Layout

1. **Create Dashboard Sheet**:
   - Insert → Dashboard (or use a blank sheet)
   - Set up the layout similar to your original

2. **Add Charts and Tables**:
   - Copy/paste or link the charts you created
   - Position them in the dashboard layout
   - Add titles and labels

3. **Add KPI Cards**:
   - Create summary cards for key metrics
   - Use conditional formatting for color coding
   - Examples:
     - Total Incidents (current month)
     - System Availability %
     - Backup Success Rate
     - Server Health Score

## Step 4: Add Interactivity

1. **Slicers**:
   - Insert → Slicer
   - Connect to date fields for time filtering
   - Connect to status fields for status filtering
   - Position slicers on dashboard

2. **Timeline**:
   - Insert → Timeline
   - Connect to date fields
   - Enable period selection (day, month, quarter, year)

3. **Buttons**:
   - Add buttons for common actions
   - Link to refresh data macros
   - Add navigation buttons

## Step 5: Automate Data Refresh

1. **Set Refresh Schedule**:
   - Data → Queries & Connections
   - Right-click query → Properties
   - Set "Refresh every X minutes"
   - Enable "Refresh data when opening the file"

2. **Create Refresh Macro**:
   ```vba
   Sub RefreshAllData()
       ActiveWorkbook.RefreshAll
   End Sub
   ```
   - Assign to a button on the dashboard

## Step 6: Preserve Original Excel Features

### Recreate Formulas

1. **Calculated Columns**:
   - Add calculated columns in Power Query
   - Or add formulas in Excel tables
   - Examples:
     - Duration = End - Start
     - Success Rate = (Success / Total) * 100

2. **Conditional Formatting**:
   - Recreate the conditional formatting rules
   - Use data bars, color scales, icon sets
   - Apply to key metrics

### Recreate Validation

1. **Data Validation**:
   - Data → Data Validation
   - Set dropdown lists for status fields
   - Add input messages and error alerts

## Step 7: Test and Optimize

1. **Test Refresh**:
   - Refresh data from MySQL
   - Verify charts update correctly
   - Check formulas still work

2. **Performance Optimization**:
   - Use Power Query for data transformation
   - Limit data import to recent periods
   - Use efficient formulas
   - Consider using Data Model for large datasets

## Alternative: Use Excel Templates

1. **Save as Template**:
   - File → Save As → Excel Template (.xltx)
   - This preserves your dashboard structure
   - Can be reused with fresh data imports

2. **Create Connection File**:
   - Save ODBC connection as .odc file
   - Reuse connection settings
   - Share with other users

## Best Practices

1. **Data Refresh Strategy**:
   - Schedule refreshes during off-peak hours
   - Use incremental refresh if possible
   - Monitor refresh performance

2. **Dashboard Design**:
   - Keep it simple and focused
   - Use consistent color schemes
   - Add clear labels and titles
   - Make it mobile-friendly if needed

3. **Security**:
   - Use read-only database user
   - Protect dashboard structure
   - Limit access to sensitive data
   - Use row-level security if needed

## Troubleshooting

### Charts Not Updating
- Check if data is refreshing properly
- Verify chart data source ranges
- Ensure calculated columns are updating

### Performance Issues
- Reduce data import size
- Use Power Query for filtering
- Optimize formulas
- Consider using Data Model

### Layout Issues
- Check print settings
- Adjust page breaks
- Use consistent sizing
- Test on different screen sizes

## Next Steps

1. Run the SQL script to create the excel_user
2. Set up the ODBC connection with the new user
3. Import your data tables
4. Recreate your dashboard following this guide
5. Test the refresh functionality
6. Share with stakeholders
