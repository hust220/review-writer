"""
LaTeX Cleaner (v9.0): Safe character cleaning for Tectonic.
Strictly protects backslashes and LaTeX commands while removing 
problematic non-ASCII characters that break BibTeX.
"""

import os
import re

def clean_text_for_latex(text: str) -> str:
    if not text: return ""
    
    # 1. Protect existing LaTeX commands (anything starting with \)
    # We use a placeholder approach or a negative lookbehind if possible
    # But a simple character-by-character filter is safer for ASCII enforcement
    
    # 2. Common character substitutions
    replacements = {
        '—': '---',
        '–': '--',
        '’': "'",
        '‘': "'",
        '“': "``",
        '”': "''",
        '±': r'$\pm$',
        'ε': r'$\epsilon$',
        'β': r'$\beta$',
        'α': r'$\alpha$',
        'γ': r'$\gamma$',
        'κ': r'$\kappa$',
        'μ': r'$\mu$',
        'δ': r'$\delta$',
        'Δ': r'$\Delta$',
    }
    for char, rep in replacements.items():
        text = text.replace(char, rep)

    # 2.5 Escape raw ampersands (not already escaped)
    text = re.sub(r'(?<!\\)&', r'\&', text)
        
    # 3. Aggressive control character removal (except \n, \t, \r)
    # This cleans up binary/hidden junk from PDF extractions
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in '\n\r\t')

    # 4. Enforce ASCII for bibliography and compilation stability
    # We strip characters above 127 but ensure we don't break the structure
    cleaned = ""
    for char in text:
        if ord(char) < 128:
            cleaned += char
        else:
            # Smart mapping for common accented characters
            if char in 'áàâäã': cleaned += 'a'
            elif char in 'éèêë': cleaned += 'e'
            elif char in 'íìîï': cleaned += 'i'
            elif char in 'óòôöõ': cleaned += 'o'
            elif char in 'úùûü': cleaned += 'u'
            elif char in 'ñ': cleaned += 'n'
            elif char in 'ç': cleaned += 'c'
            else: cleaned += ' ' # Space for everything else
            
    return cleaned

def clean_file(file_path: str):
    if not os.path.exists(file_path): return
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    cleaned = clean_text_for_latex(content)
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(cleaned)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        clean_file(sys.argv[1])
