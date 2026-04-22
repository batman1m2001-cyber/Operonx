"""Compare visual appearance of different operator options."""

# Python operator precedence (relevant ones):
#   14: @
#   13: *, /, //, %
#   12: +, -
#   11: <<, >>
#   10: &
#    9: ^
#    8: |

# For soft edge to work with >> hard edge, we need precedence > 11
# Options: @ (14), * (13), / (13), // (13), % (13)

print("=" * 60)
print("Visual comparison of operator syntax for workflow edges")
print("=" * 60)

print("""
Current (broken):
    START >> branch > [case1, case2] > merge >> END

Option 1: @ for soft edge (precedence 14)
    START >> branch @ [case1, case2] @ merge >> END
    branch @ case1 >> merge

Option 2: * for soft edge (precedence 13)
    START >> branch * [case1, case2] * merge >> END
    branch * case1 >> merge

Option 3: / for soft edge (precedence 13)
    START >> branch / [case1, case2] / merge >> END
    branch / case1 >> merge

Option 4: % for soft edge (precedence 13)
    START >> branch % [case1, case2] % merge >> END
    branch % case1 >> merge

Option 5: Keep >> for hard, document "no mixing in single chain"
    START >> branch
    branch > [case1, case2] > merge
    merge >> END

Option 6: Use | for soft (lower precedence 8, but looks like "or/branch")
    START >> branch | [case1, case2] | merge >> END
    (Won't work due to precedence, but looks nice)

Option 7: Reverse - use >> for soft (more common), > for hard
    START > branch >> [case1, case2] >> merge > END
    branch >> case1 > merge  # This works!
""")

print("\nMy ranking for visual clarity:")
print("1. Option 7 (swap roles) - >> for soft, > for hard")
print("   Pros: Both operators familiar, >> looks like 'branching out'")
print("   Cons: Only works for soft-then-hard, not hard-then-soft")
print()
print("2. Option 5 (no mixing) - separate lines")
print("   Pros: No new operators, always works")
print("   Cons: More verbose")
print()
print("3. Option 2 (*) - multiplication symbol")
print("   Pros: Works with precedence")
print("   Cons: Could be confused with multiplication")
