# -*- coding: utf-8 -*-
import sys, io, types
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
master=cl.load_master_content(); tmap=cl.load_thinkific_map()
def fc(reply):
    def chat(model,messages,timeout=None,max_tokens=None,fast=False,**kw):
        chat.msgs=messages; chat.n=getattr(chat,'n',0)+1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))])
    chat.n=0
    return chat
class Ocr:
    def __init__(s,t): s.t=t
    def __call__(s,b): return (s.t,0.96)

print("=== test5: '2. zadatka' -> image_test solves item 2 ===")
c=fc("Rezultat 2. zadatka: -6/7.")
out=svc.handle_chat({'grade':6,'mode':'quick','student_message':'Daj mi samo rezultat 2. zadatka sa slike.'},
  c, master, tmap, model='m', timeout=1, image_bytes=b'x', ocr_image=Ocr("1. 3/4 + 1/4\n2. -2/7 - 4/7\n3. 5 - 8"))
print("answer:",out['answer'])
print("model called:",c.n,"| next_state kind:",out['next_state']['active_task_kind'])
print("next image_test:",out['next_state'].get('image_test'))
print("user prompt has current item?:", '2' in c.msgs[-1][-1]['content'][:400])

print("\n=== test3: 'Testovi matematika 8' single task -> does NOT refuse ===")
c2=fc("(x+3)^2 = x^2 + 6x + 9.")
out2=svc.handle_chat({'grade':6,'mode':'quick','student_message':'Daj mi rezultat sa slike.'},
  c2, master, tmap, model='m', timeout=1, image_bytes=b'x', ocr_image=Ocr("Testovi matematika 8\nIzračunaj (x+3)^2."))
print("status:",out2['status'],"| final_topic:",out2['final_topic'],"| model called:",c2.n)
sp=c2.msgs[0]['content']
print("sys no-refuse instr:", 'ne odbijaj valjan' in sp.lower())
