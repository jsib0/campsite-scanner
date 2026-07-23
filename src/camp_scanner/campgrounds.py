from dataclasses import dataclass

BASE_URL = "https://www.recreation.gov"


@dataclass(frozen=True)
class Campground:
    name: str
    campground_id: str

    @property
    def booking_url(self) -> str:
        return f"{BASE_URL}/camping/campgrounds/{self.campground_id}"


CAMPGROUNDS = (
    Campground("Upper Pines", "232447"),
    Campground("Lower Pines", "232450"),
    Campground("North Pines", "232449"),
    Campground("Camp 4", "10004152"),
)
