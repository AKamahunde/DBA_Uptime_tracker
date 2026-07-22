import os

for root, dirs, files in os.walk('E:\\dba\\CascadeProjects\\windsurf-project'):
    for file in files:
        if file.endswith('.py'):
            try:
                with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'availability_records' in content or 'availability' in content.lower():
                        print(f"Found in: {os.path.join(root, file)}")
            except:
                pass
