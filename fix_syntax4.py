with open('investment_engine/main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and remove the duplicate except block
new_lines = []
skip_next = False
for i, line in enumerate(lines):
    # Skip the duplicate except block
    if i > 0 and 'except Exception as e:' in line and 'Regime analysis failed' in lines[i-1]:
        # Check if previous line was also an except block
        if i > 1 and 'except Exception as e:' in lines[i-2] and 'Regime analysis failed' in lines[i-1]:
            continue  # Skip this duplicate line
        if i < len(lines) - 1 and 'Regime analysis failed' in lines[i+1] and 'except Exception as e:' in lines[i+2]:
            continue  # Skip the next except line too
    new_lines.append(line)

with open('investment_engine/main.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Fixed!')