with open('investment_engine/main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Remove lines 854 and 855 (1-indexed), which are the duplicate except block
# 0-indexed: remove indices 853 and 854
new_lines = []
for i, line in enumerate(open('investment_engine/main.py', 'r', encoding='utf-8').readlines()):
    # Skip the duplicate except block (lines 854-855 in 1-indexed = indices 853, 854)
    if i not in (853, 854):  # 0-indexed
        print(i, repr(line[:80]))

with open('investment_engine/main.py', 'w', encoding='utf-8') as f:
    f.writelines([l for i, l in enumerate(open('investment_engine/main.py', 'r', encoding='utf-8').readlines()) if i not in (853, 854)])
print('Fixed!')