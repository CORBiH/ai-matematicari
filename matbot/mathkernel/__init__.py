"""Čisto matematičko jezgro (Batch #4) — egzaktni rješavači bez Practice-a.

GRANICA OVOG PAKETA: nijedan modul ovdje ne smije poznavati lekciju, naslov,
MCQ, sesiju ni Practice paket. Ulaz je strukturisan matematički problem
(IR), izlaz je egzaktno, dokazivo rješenje. Practice generatori
(matbot/deterministic/*) su POTROŠAČI ovog jezgra — oni renderuju zadatke i
opcije; buduci „Daj mi rezultat" mod je drugi predviđeni potrošač i zato
ovaj sloj ne smije imati nijednu Practice zavisnost.

MATEMATIČKI AUTORITET: isključivo egzaktna aritmetika (`fractions.Fraction`,
cijeli brojevi). Binarni float ne postoji u ovom paketu.
"""
