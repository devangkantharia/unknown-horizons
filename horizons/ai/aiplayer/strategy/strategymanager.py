# ###################################################
# Copyright (C) 2012 The Unknown Horizons Team
# team@unknown-horizons.org
# This file is part of Unknown Horizons.
#
# Unknown Horizons is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the
# Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
# ###################################################

import logging

from horizons.ai.aiplayer.strategy.mission.chaseshipsandattack import ChaseShipsAndAttack
from horizons.ai.aiplayer.strategy.mission.pirateroutine import PirateRoutine
from horizons.ai.aiplayer.strategy.mission.scouting import ScoutingMission
from horizons.ai.aiplayer.strategy.mission.surpriseattack import SurpriseAttack
from horizons.component.namedcomponent import NamedComponent
from horizons.util.worldobject import WorldObject


class StrategyManager(object):
	"""
	StrategyManager object is responsible for handling major decisions in game such as
	sending fleets to battle, keeping track of diplomacy between players, declare wars.
	"""
	log = logging.getLogger("ai.aiplayer.fleetmission")

	def __init__(self, owner):
		super(StrategyManager, self).__init__()
		self.__init(owner)

	def __init(self, owner):
		self.owner = owner
		self.world = owner.world
		self.session = owner.session
		self.unit_manager = owner.unit_manager
		self.missions = set()

		# Dictionary of Condition_hash => FleetMission. Condition_hash is a key since it's searched for more often. Values are
		# unique because of WorldObject's inheritance, but it makes removing items from it in O(n).
		self.conditions_being_resolved = {}

		self.missions_to_load = {
			ScoutingMission: "ai_scouting_mission",
			SurpriseAttack: "ai_mission_surprise_attack",
			ChaseShipsAndAttack: "ai_mission_chase_ships_and_attack",
		}

	@property
	def conditions(self):
		# conditions are held in behavior manager since they are a part of behavior profile (just like actions and strategies)
		return self.owner.behavior_manager.get_conditions()

	def save(self, db):
		for mission in list(self.missions):
			mission.save(db)

		for condition, mission in self.conditions_being_resolved.iteritems():
			db("INSERT INTO ai_condition_lock (owner_id, condition, mission_id) VALUES(?, ?, ?)", self.owner.worldid, condition, mission.worldid)

	@classmethod
	def load(cls, db, owner):
		self = cls.__new__(cls)
		super(StrategyManager, self).__init__()
		self.__init(owner)
		self._load(db)
		return self

	def _load(self, db):
		for class_name, db_table in self.missions_to_load.iteritems():
			db_result = db("SELECT m.rowid FROM %s m, ai_fleet_mission f WHERE f.owner_id = ? and m.rowid = f.rowid" % db_table, self.owner.worldid)
			for (mission_id,) in db_result:
				self.missions.add(class_name.load(mission_id, self.owner, db, self.report_success, self.report_failure))

		#TODO: Kept for debugging purposes, remove later
		"""
		db_result = db("SELECT m.rowid FROM ai_scouting_mission m, ai_fleet_mission f WHERE f.owner_id = ? and m.rowid = f.rowid", self.owner.worldid)
		for (mission_id,) in db_result:
			self.missions.add(ScoutingMission.load(mission_id, self.owner, db, self.report_success, self.report_failure))

		db_result = db("SELECT m.rowid FROM ai_mission_surprise_attack m, ai_fleet_mission f WHERE f.owner_id = ? and m.rowid = f.rowid", self.owner.worldid)
		for (mission_id,) in db_result:
			self.missions.add(SurpriseAttack.load(mission_id, self.owner, db, self.report_success, self.report_failure))

		db_result = db("SELECT m.rowid FROM ai_mission_chase_ships_and_attack m, ai_fleet_mission f WHERE f.owner_id = ? and m.rowid = f.rowid", self.owner.worldid)
		for (mission_id,) in db_result:
			self.missions.add(ChaseShipsAndAttack.load(mission_id, self.owner, db, self.report_success, self.report_failure))
		"""

		# load condition locks
		db_result = db("SELECT condition, mission_id FROM ai_condition_lock WHERE owner_id = ?", self.owner.worldid)
		for (condition, mission_id) in db_result:
			self.conditions_being_resolved[condition] = WorldObject.get_object_by_id(mission_id)

	def report_success(self, mission, msg):
		self.log.info("Player: %s|StrategyManager|Mission %s was a success: %s", self.owner.worldid, mission, msg)
		self.end_mission(mission)

	def report_failure(self, mission, msg):
		self.log.info("Player: %s|StrategyManager|Mission %s was a failure: %s", self.owner.worldid, mission, msg)
		self.end_mission(mission)

	def end_mission(self, mission):
		self.log.info("Player: %s|StrategyManager|Mission %s ended", self.owner.worldid, mission)
		if mission in self.missions:
			self.missions.remove(mission)

		# remove condition lock (if condition was lockable) after mission ends
		self.unlock_condition(mission)

	def start_mission(self, mission):
		self.log.info("Player: %s|StrategyManager|Mission %s started", self.owner.worldid, mission)
		self.missions.add(mission)
		mission.start()

	def lock_condition(self, condition, mission):
		self.conditions_being_resolved[condition] = mission

	def unlock_condition(self, mission):
		# values (FleetMission) are unique so it's possible to remove them this way:
		for condition, value in self.conditions_being_resolved.iteritems():
			if mission == value:
				del self.conditions_being_resolved[condition]
				return

	def get_missions(self, condition=None):
		"""
		Get missions filtered by certain condition (by default return all missions)
		"""
		if condition:
			return [mission for mission in self.missions if condition(mission)]
		else:
			return self.missions

	def request_to_pause_mission(self, mission, **environment):
		"""
		@return: returns True is mission is allowed to pause, False otherwise
		@rtype: bool
		"""
		# TODO: make that decision based on environment (**environment as argument)
		mission.pause_mission()
		return True

	def get_ships_for_mission(self):
		filters = self.unit_manager.filtering_rules
		rules = (filters.ship_state((self.owner.shipStates.idle,)), filters.fighting(), filters.not_in_fleet())
		idle_ships = self.unit_manager.get_ships(rules)

		return idle_ships

	def handle_strategy(self):

		# Get all available ships that can take part in a mission
		idle_ships = self.get_ships_for_mission()

		# Get all other players
		other_players = [player for player in self.session.world.players if player != self.owner]

		# Check which conditions occur
		occuring_conditions = []

		environment = {'idle_ships': idle_ships}

		for player in other_players:
			# Prepare environment
			self.log.debug("Conditions occuring against player %s", player.name)
			environment['player'] = player

			for condition in self.conditions.keys():

				# Check whether given condition is already being resolved
				if condition.get_identifier(**environment) in self.conditions_being_resolved:
					self.log.debug("  %s: Locked", condition.__class__.__name__)
					continue

				condition_outcome = condition.check(**environment)
				self.log.debug("  %s: %s", (condition.__class__.__name__, ("Yes" if condition_outcome else "No")))
				if condition_outcome:
					occuring_conditions.append((condition, condition_outcome))

			# Revert environment to previous state
			del environment['player']

		# Nothing to do when none of the conditions occur
		if occuring_conditions:
			# Choose the most important one

			selected_condition, selected_outcome = max(occuring_conditions,
				key=lambda (condition, outcome): self.conditions[condition] * outcome['certainty'])

			self.log.debug("Selected condition: %s", selected_condition.__class__.__name__)
			for key, value in selected_outcome.iteritems():
				# Insert condition-gathered info into environment
				environment[key] = value
				self.log.debug(" %s: %s", (key, value))

			# Try to execute a mission that resolves given condition the best
			mission = self.owner.behavior_manager.request_strategy(**environment)
			if mission:
				self.start_mission(mission)
				if selected_condition.lockable:
					self.lock_condition(selected_condition.get_identifier(**environment), mission)

		self.log.debug("Missions:")
		for mission in list(self.missions):
			self.log.debug("%s", mission)

		self.log.debug("Fleets:")
		for fleet in list(self.unit_manager.fleets):
			self.log.debug("%s", fleet)

	def tick(self):
		self.handle_strategy()


class PirateStrategyManager(StrategyManager):

	def __init__(self, owner):
		super(PirateStrategyManager, self).__init__(owner)
		self.__init(owner)

	def get_ships_for_mission(self):
		filters = self.unit_manager.filtering_rules
		rules = (filters.ship_state((self.owner.shipStates.idle,)), filters.pirate(), filters.not_in_fleet())
		idle_ships = self.unit_manager.get_ships(rules)

		return idle_ships

	@classmethod
	def load(cls, db, owner):
		self = cls.__new__(cls)
		super(PirateStrategyManager, self).__init__(owner)
		self.__init(owner)
		self._load(db)
		return self

	def __init(self, owner):
		self.missions_to_load = {
			PirateRoutine: "ai_mission_pirate_routine",
		}
