import re

with open('investment_engine/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the duplicate except block
old = '''        except Exception as e:
            regime_context = f"Regime analysis failed: {e}"

        except Exception as e:
            regime_context = f"Regime analysis failed: {e}"

        # Task A: Build fundamentals pack'''

new = '''        except Exception as e:
            regime_context = f"Regime analysis failed: {e}"

        # Task A: Build fundamentals pack'''

content = content.replace(old, new)

with open('investment_engine/main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed!')
"