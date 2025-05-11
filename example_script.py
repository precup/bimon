#!/usr/bin/env python

import random
import sys

# See what gets passed in (executable path, arguments)
print("Argv:", sys.argv)

# Print a random test result
print("Random choice:", random.choice(["mark good", "mark bad"]))

# Randomly crash or not
# sys.exit(random.choice([0, 1]))