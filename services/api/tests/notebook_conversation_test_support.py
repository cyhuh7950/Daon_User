"""Test-only admission ledger for HTTP DI; this is not SQL or RLS evidence."""
from datetime import datetime, timezone
from daon_user_api.notebook import NotebookError
from daon_user_api.notebook_conversation_postgres import admission_payload, _turn_id

class ConversationTestLedger:
    def __init__(self):
        self.turns={}
        self.events=[]
        self.required=[]
    def require_selected_bindings(self,context,notebook_id,*,required):
        self.required.append((context,notebook_id,required))
    def admit_question_turn(self,context,notebook_id,**values):
        payload=admission_payload(context,notebook_id,**values)
        turn_id=_turn_id(context,notebook_id,values['run_id'])
        if turn_id in self.turns and self.turns[turn_id][0]!=payload:
            raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
        self.turns.setdefault(turn_id,(payload,datetime.now(timezone.utc)))
        return turn_id
    def append_question_turn_event(self,context,notebook_id,**values):
        entry=(context,notebook_id,values)
        if entry not in self.events: self.events.append(entry)
    def list_conversation_turns(self,context,notebook_id,*,limit=50,before=None):
        return {'items':[{'turn_id':key,'question':payload['question'],'response':None,'state':'pending',
                         'submitted_at':now.isoformat().replace('+00:00','Z'),'model':None,'client_request_id':payload.get('client_request_id')}
            for key,(payload,now) in self.turns.items() if payload['actor_id']==context.actor_id and payload['notebook_id']==notebook_id],
                'next_cursor':None}
