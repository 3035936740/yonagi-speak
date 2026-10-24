from pathlib import Path
import tempfile
import unittest
from bot_commands import annotate,invocation
from member_labels import MemberLabels
from member_policy import model_view,MemberBlocked


class BotCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        MemberLabels(self.folder).mark('22222','bot')

    def item(self,mid,text,uid='11111',stamp=1,**extra):
        return dict(_id=mid,text=text,_uid=uid,_time=stamp,**extra)

    def test_learn_ya_from_known_bot_at_responses_and_preserve_human(self):
        rows=[self.item('1','ya stm'),self.item('2','success','22222',20),
              self.item('3','ya wd black',stamp=28),self.item('4','done','22222',32,_at_ids=['11111'])]
        annotate(self.folder,[],rows,{},'99999')
        future=self.item('5','ya wd white',stamp=5)
        annotate(self.folder,rows,[future],{},'99999')
        self.assertTrue(all(row.get('_bot_command') for row in [rows[0],rows[2],future]))
        self.assertNotIn('11111',MemberLabels(self.folder).facts())
        normal=self.item('6','hello everyone',stamp=6)
        annotate(self.folder,rows,[normal],{},'99999')
        self.assertNotIn('_bot_command',normal)
        with self.assertRaises(MemberBlocked):model_view(self.folder,rows,dict(rows[2],_bot_command=None))
        context,_=model_view(self.folder,rows,normal)
        self.assertNotIn('3',[row['_id'] for row in context])

    def test_explicit_self_and_ordinary_words_are_not_commands(self):
        self.assertIsNone(invocation(self.item('1','yes hello'),{}))
        self.assertIsNone(invocation(self.item('1','/help',at_bot=True),{},self_id='99999'))
        self.assertIsNone(invocation(self.item('1','/help',_at_ids=['99999']),{},self_id='99999'))
        self.assertIsNone(invocation(self.item('1','please /help'),{}))
        self.assertIsNotNone(invocation(self.item('1','！help'),{}))

    def test_ambiguous_unanchored_response_does_not_learn(self):
        rows=[self.item('1','ya stm'),self.item('other','hello','33333',2),self.item('2','success','22222',3)]
        routes=annotate(self.folder,[],rows,{},'99999')
        self.assertEqual(routes,{})
        self.assertNotIn('_bot_command',rows[0])

    def test_exact_reply_wins_over_later_conversation(self):
        rows=[self.item('1','ya stm'),self.item('2','normal chat',stamp=2),
              self.item('3','success','22222',3,_reply_id='1',_at_ids=['11111'])]
        annotate(self.folder,[],rows,{},'99999')
        self.assertIn('_bot_command',rows[0]);self.assertNotIn('_bot_command',rows[1])


if __name__=='__main__':unittest.main()
