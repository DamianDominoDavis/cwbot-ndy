from cwbot.common.exceptions import FatalError
from cwbot.locks import InventoryLock
from cwbot.modules.BaseKmailModule import BaseKmailModule
from cwbot.util.textProcessing import toTypeOrNone
from cwbot.util.tryRequest import tryRequest
from cwbot.kolextra.functions.equipCustomOutfitByName \
                       import equipCustomOutfitByName
from kol.database.SkillDatabase import getSkillFromId
from kol.request.EquipRequest import EquipRequest
from kol.request.StatusRequest import StatusRequest
from kol.request.UseSkillRequest import UseSkillRequest
import kol.Error


def _integerize(map_, keyname, defaultval, desc):
    try:
        map_[keyname] = int(map_.setdefault(keyname, defaultval))
    except ValueError:
        raise FatalError("Invalid value for key '{}' in {}: {}"
                         .format(keyname, desc, map_[keyname]))
    

class BuffbotModule(BaseKmailModule):
    """ 
    A module that works as a buffbot. Note that if you want to use this
    module, you need a HealingModule configured as well.
    
    To configure: use the following format:

    [[[Buffbot]]]
        type = messages.BuffbotModule
        restore_mp_to_percent = 80 # heal to this MP percent
        [[[[buffs]]]]
            [[[[[Ode 25]]]]]
                id = 6014
                mp_cost = 50 # filled out automatically but you can override
                cost = 1
                casts = 1
                daily_limit = 2
                outfit = AT_outfit
                description = "25 turns of The Ode To Booze"
            [[[[[Fat Leon 2000]]]]]
                id = 6010
                cost = 1000
                casts = 80
                daily_limit = 0
                outfit = AT_outfit
                description = "2000 turns of Fat Leon's Phat Loot Lyric"
    """
    requiredCapabilities = ['kmail']
    _name = "buffbot"

    def __init__(self, manager, identity, config):
        self._buffs = {}
        self._used = {}
        self._mpMax = None
        self._healer = None
        super(BuffbotModule, self).__init__(manager, identity, config)

        
    def _configure(self, config):
        self._buffs = {}
        self._outfits = {}
        healer = toTypeOrNone(config.setdefault('healer', "none"), str)
        if healer is not None:
            healer = "__" + healer + "__"
        self._healer = healer
        try:
            self._mpMax = int(config.setdefault('restore_mp_to_percent', 80))
        except ValueError:
            raise FatalError("Invalid restore_mp_to_percent: {}"
                             .format(config['restore_mp_to_percent']))
        
        buffs = config.setdefault('buffs', {'The Ode To Booze': {
                                                'id': 6014,
                                                'cost': 1,
                                                'casts': 1,
                                                'daily_limit': 2,
                                                'outfit': 'none'}})
        for k,v in buffs.items():
            _integerize(v, 'id', "UNKNOWN", k)
            _integerize(v, 'cost', "UNKNOWN", k)
            _integerize(v, 'casts', "UNKNOWN", k)
            _integerize(v, 'daily_limit', "0", k)
            v.setdefault('outfit', "none")
            v.setdefault('description', "none")
            try:
                if "mp_cost" not in v:
                    skill = getSkillFromId(str(v['id']), self.session)
                    v['mp_cost'] = skill['mpCost']
                else:
                    _integerize(v, 'mp_cost', "UNKNOWN", k)
            except kol.Error.Error:
                _integerize(v, 'mp_cost', "UNKNOWN", k)
            self._buffs[k] = v
        priceList = [v['cost'] for v in self._buffs.values()
                     if v['cost'] > 0]
        if len(priceList) != len(set(priceList)):
            raise FatalError("Duplicate buff prices for module {}"
                             .format(self.id))
            
            
    def initialize(self, state, initData):
        self._used = {}
        if state['date'] == self._currentKolDate():
            self._used = dict((int(k), v) for k,v in state['used'].items())
            
            
    def _currentKolDate(self):
        r = StatusRequest(self.session)
        d = tryRequest(r)
        return int(d['daysthisrun'])


    def _sendBuffKmail(self, message):
        txt = "The following buffs are available:\n"
        spellList = set(v['id'] for v in self._buffs.values())
        for sid in spellList:
            matches = [(k,v) for k,v in self._buffs.items()
                       if v['id'] == sid and v['cost'] > 0]
            matches.sort(key=lambda x: x[1]['cost'])
            for k,v in matches:
                desc = toTypeOrNone(v['description'])
                if desc is None:
                    desc = "{} castings of {}".format(v['casts'], k)
                lim = v['daily_limit']
                limTxt = "" if lim == 0 else " ({} per day)".format(lim)
                cost = v['cost']
                txt += "\n{} meat for {}{}".format(cost, desc, limTxt)
            txt += "\n"
        m = self.newMessage(message.uid, txt)
        return m
        
        
    def _buff(self, uid, buff):
        n = buff['casts']
        cost = buff['mp_cost']
        mpRequired = n * cost
        r2 = StatusRequest(self.session)
        d2 = self.tryRequest(r2)
        mpBefore = int(d2['mp'])
        self.log("Preparing to cast buff {} (requires {} mp)"
                 .format(buff['description'], mpRequired))
        if mpBefore < mpRequired:
            try:
                self.debugLog("Requesting healing from module {}"
                              .format(self._healer))
                replies = self._raiseEvent("heal", self._healer, 
                                           {'type': 'mp', 
                                            'points': mpRequired,
                                            'percent': self._mpMax})
                healResult = replies[-1].data
                mpBefore = healResult['mp']
            except IndexError:
                raise FatalError("Invalid healer {}".format(self._healer))

        self.log("Casting skill {} x{}".format(buff['id'], n))
        r1 = UseSkillRequest(self.session, str(buff['id']), n, uid)
        _d1 = self.tryRequest(r1, numTries=1)
        r2 = StatusRequest(self.session)
        d2 = self.tryRequest(r2)
        mpAfter = int(d2['mp'])
        self.log("Used {} mp. (Now at {}/{})"
                 .format(mpBefore - mpAfter, mpAfter, d2['maxmp']))
        return mpBefore - mpAfter 
        
        
    def _equipForBuff(self, buff):
        outfitName = toTypeOrNone(buff['outfit'], str)
        if outfitName is None:
            return
        equipCustomOutfitByName(self.session, outfitName)
            
            
    def _doBuff(self, uid, buff):
        self._equipForBuff(buff)
        return self._buff(uid, buff)
        
        
    def _processKmail(self, message):
        items = message.items
        uid = message.uid
        meat = message.meat
        text = message.text
        if message.items:
            return None
        if message.text.strip() != "":
            return None
        if meat == 0:
            if text.lower() == "buffs":
                return self._sendBuffKmail(message)
            return None
        priceList = [v['cost'] for v in self._buffs.values()]
        if meat not in priceList:
            return None
            
        selectedBuff = [v for v in self._buffs.values() 
                        if v['cost'] == meat][0]
        
        # check for use limit
        usesDict = self._used.get(uid, {})
        numUses = usesDict.get(meat, 0)
        limit = selectedBuff['daily_limit']
        if numUses >= limit and limit > 0:
            return self.newMessage(
                    uid, "Sorry, you have used your daily limit for "
                         "that spell today.", meat)
        
        with InventoryLock.lock:
            # prepare for buff
            try:
                spent = self._doBuff(uid, selectedBuff)
                if spent == 0:
                    raise kol.Error.Error(
                        "Could not cast buff for some reason", 
                        kol.Error.REQUEST_GENERIC)
            except kol.Error.Error as e:
                for uid in self.properties.getAdmins("buffbot_admin"):
                    self.sendKmail(uid, "Buffbot error for {}: {}"
                                        .format(selectedBuff, e.msg))
                self.log("Problem with buff: {}".format(e.msg))
                return self.newMessage(uid, 
                                       "Sorry, there was an error casting "
                                       "your buff ({}).".format(e.msg),
                                       meat)
            usesDict[meat] = numUses + 1
            self._used[uid] = usesDict
            return self.newMessage(-1)
    
    
    def _kmailDescription(self):
        return ("BUFFBOT: Send me a kmail with the text \"buffs\" for a list "
                "of available buffs. To actually request a buff, send me "
                "an appropriate amount of meat with nothing in the message "
                "text.")
        
        
    @property
    def initialState(self):
        return {'used': {}, 'date': self._currentKolDate()}
        
        
    @property
    def state(self):
        return {'used': self._used, 'date': self._currentKolDate()}
        
        
    def _eventCallback(self, eData):
        if eData.subject == "state":
            self._eventReply(self.state)    
        