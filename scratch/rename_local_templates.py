import os

templates_dir = r"e:\dba\CascadeProjects\windsurf-project\backend\api\templates"

replacements = {
    "DBA Operations Tracker": "Database Monitoring Tool",
    "Database Management Hub": "Database Monitoring Tool"
}

modified_files = 0

for root, dirs, files in os.walk(templates_dir):
    for file in files:
        if file.endswith(".html"):
            filepath = os.path.join(root, file)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            new_content = content
            for old, new in replacements.items():
                new_content = new_content.replace(old, new)
            
            if new_content != content:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"Updated: {filepath}")
                modified_files += 1

print(f"Completed! Renamed titles/headers in {modified_files} local template files.")
