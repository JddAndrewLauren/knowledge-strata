"""Corpus adapters: folder in, ``Record``s out (design.md, "Module map").

Three adapters share this package - ``sources``, ``notes`` and
``manuscript`` - and nothing else. Each owns one folder shape and knows
nothing about the index; the index takes Records and nothing else.
"""
