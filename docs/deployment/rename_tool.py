import os

directory = r"e:\dba\CascadeProjects\windsurf-project\backend\api\templates"
old_text = "DBA Operations Tracker"
new_text = "Database Monitoring Tool"

count = 0
for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith(".html"):
            path = os.path.join(root, file)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if old_text in content:
                content = content.replace(old_text, new_text)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"Updated: {path}")
                count += 1

print(f"Total files updated: {count}")
