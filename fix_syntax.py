with open('investment_engine/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            regime_context = _build_regime_context(regime_result)
        except Exception as e:
            regime_context = f"Regime analysis failed: {e}"

        except Exception as e:
            regime_context = f"Regime analysis failed: {e}"

        # Task A: Build fundamentals pack (Appendix D) - §5.2 pack with TTM, margins, FCF, ROIC, growth'''

new = '''            regime_context = _build_regime_context(regime_result)
        except Exception as e:
            regime_context = f"Regime analysis failed: {e}"

        # Task A: Build fundamentals pack (Appendix D) - §5.2 pack with TTM, margins, FCF, ROIC, growth'''

content = content.replace(old, new)
with open('investment_engine/main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed!')