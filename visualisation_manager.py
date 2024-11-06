# Branch: ProsumerJack
# File: visualisation_manager.py

from multiprocessing import Process, Queue, Event
from energy_types import EnergyReading
from dataAnalysis import calculate_end_date, update_plot_same

class VisualisationManager:
    def __init__(self, start_date, timescale):
        self.queue = Queue()
        self.ready_event = Event()
        self.plot_process = None
        self.start_date = start_date
        self.timescale = timescale

    def start(self, df):
        # start the plot process
        end_date = calculate_end_date(self.start_date, self.timescale)
        self.plot_process = Process(
            target = update_plot_same,
            args = (df, self.start_date, end_date, self.timescale, self.queue, self.ready_event)
        )
        self.plot_process.start()
        self.ready_event.wait()  # Wait for the plot to be initialised

    def update(self, reading: EnergyReading):
        # update the plot with new data
        self.queue.put({
            'timestamp': reading.timestamp,
            'generation': reading.generation if hasattr(reading, 'generation') else 0,
            'demand': reading.demand
        })

    def stop(self):
        # stop the plot process
        self.queue.put("done")
        if self.plot_process:
            self.plot_process.join()