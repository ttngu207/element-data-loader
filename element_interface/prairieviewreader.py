from collections import defaultdict
import pathlib
import xml.etree.ElementTree as ET
from datetime import datetime

import numpy as np


def get_pv_metadata(pvtiffile: str) -> dict:
    """Extract metadata for scans generated by PrairieView acquisition software.

    The PrairieView software generates one .ome.tif imaging file per frame acquired. The
    metadata for all frames is contained one .xml file. This function locates the .xml
    file and generates a dictionary necessary to populate the DataJoint ScanInfo and
    Field tables. PrairieView works with resonance scanners with a single field.
    PrairieView does not support bidirectional x and y scanning. ROI information is not
    contained in the .xml file. All images generated using PrairieView have square
    dimensions(e.g. 512x512).

    Args:
        pvtiffile: An absolute path to the .ome.tif image file.

    Raises:
        FileNotFoundError: No .xml file containing information about the acquired scan
            was found at path in parent directory at `pvtiffile`.

    Returns:
        metainfo: A dict mapping keys to corresponding metadata values fetched from the
            .xml file.
    """

    # May return multiple xml files. Only need one that contains scan metadata.
    xml_files = pathlib.Path(pvtiffile).parent.glob("*.xml")

    for xml_file in xml_files:
        tree = ET.parse(xml_file)
        root = tree.getroot()
        if root.find(".//Sequence"):
            break
    else:
        raise FileNotFoundError(
            f"No PrarieView metadata XML file found at {pvtiffile.parent}"
        )

    bidirectional_scan = False  # Does not support bidirectional
    roi = 1
    n_fields = 1  # Always contains 1 field
    record_start_time = root.find(".//Sequence/[@cycle='1']").attrib.get("time")

    # Get all channels and find unique values
    channel_list = [
        int(channel.attrib.get("channel"))
        for channel in root.iterfind(".//Sequence/Frame/File/[@channel]")
    ]
    n_channels = len(set(channel_list))
    n_frames = len(root.findall(".//Sequence/Frame"))
    framerate = 1 / float(
        root.findall('.//PVStateValue/[@key="framePeriod"]')[0].attrib.get("value")
    )  # rate = 1/framePeriod

    usec_per_line = (
        float(
            root.findall(".//PVStateValue/[@key='scanLinePeriod']")[0].attrib.get(
                "value"
            )
        )
        * 1e6
    )  # Convert from seconds to microseconds

    scan_datetime = datetime.strptime(root.attrib.get("date"), "%m/%d/%Y %I:%M:%S %p")

    total_duration = float(
        root.findall(".//Sequence/Frame")[-1].attrib.get("relativeTime")
    )

    px_height = int(
        root.findall(".//PVStateValue/[@key='pixelsPerLine']")[0].attrib.get("value")
    )
    # All PrairieView-acquired images have square dimensions (512 x 512; 1024 x 1024)
    px_width = px_height

    um_per_pixel = float(
        root.find(
            ".//PVStateValue/[@key='micronsPerPixel']/IndexedValue/[@index='XAxis']"
        ).attrib.get("value")
    )

    um_height = um_width = float(px_height) * um_per_pixel

    # x and y coordinate values for the center of the field
    x_field = float(
        root.find(
            ".//PVStateValue/[@key='currentScanCenter']/IndexedValue/[@index='XAxis']"
        ).attrib.get("value")
    )
    y_field = float(
        root.find(
            ".//PVStateValue/[@key='currentScanCenter']/IndexedValue/[@index='YAxis']"
        ).attrib.get("value")
    )
    if (
        root.find(
            ".//Sequence/[@cycle='1']/Frame/PVStateShard/PVStateValue/[@key='positionCurrent']/SubindexedValues/[@index='ZAxis']"
        )
        is None
    ):

        z_fields = np.float64(
            root.find(
                ".//PVStateValue/[@key='positionCurrent']/SubindexedValues/[@index='ZAxis']/SubindexedValue"
            ).attrib.get("value")
        )
        n_depths = 1
        assert z_fields.size == n_depths
        bidirection_z = False

    else:

        bidir_z = root.find(".//Sequence").attrib.get("bidirectionalZ")
        bidirection_z = bidir_z == 'True'

        # One "Frame" per depth. Gets number of frames in first sequence
        planes = [
            int(plane.attrib.get("index"))
            for plane in root.findall(".//Sequence/[@cycle='1']/Frame")
        ]
        n_depths = len(set(planes))

        # find z_depths controller if there is more than 1.
        if (
            len(
                root.findall(
                    ".//Sequence/[@cycle='1']/Frame/[@index='1']/PVStateShard/PVStateValue/[@key='positionCurrent']/SubindexedValues/[@index='ZAxis']/SubindexedValue"
                )
            )
            > 1
        ):

            z_dicts = [z_pos.attrib for z_pos in root.findall(".//Sequence/[@cycle='1']/Frame/PVStateShard/PVStateValue/[@key='positionCurrent']/SubindexedValues/[@index='ZAxis']/SubindexedValue")]

            z_values = [d["value"] for d in z_dicts]

            zdata_dictionary = defaultdict(list)
            for idx, val in enumerate(z_values):
                zdata_dictionary[val].append(idx)
            repeating_values = {k: v for k, v in zdata_dictionary.items() if len(v) > 1}
            if len(repeating_values) > 0:
                idx_to_drop = list(repeating_values.values())[0]
                idx_to_drop.sort(reverse=True)
                for idx in idx_to_drop:
                    del z_values[idx]
            z_values = [float(num) for num in z_values]
            z_min = min(z_values)
            z_max = max(z_values)
            z_step = z_max / n_depths
            z_fields = z_values
            assert len(z_fields) == n_depths

        else:
            z_min = float(
                root.findall(
                    ".//Sequence/[@cycle='1']/Frame/PVStateShard/PVStateValue/[@key='positionCurrent']/SubindexedValues/SubindexedValue/[@subindex='0']"
                )[0].attrib.get("value")
            )
            z_max = float(
                root.findall(
                    ".//Sequence/[@cycle='1']/Frame/PVStateShard/PVStateValue/[@key='positionCurrent']/SubindexedValues/SubindexedValue/[@subindex='0']"
                )[-1].attrib.get("value")
            )
            z_step = float(
                root.find(
                    ".//PVStateShard/PVStateValue/[@key='micronsPerPixel']/IndexedValue/[@index='ZAxis']"
                ).attrib.get("value")
            )
            z_fields = np.arange(z_min, z_max + 1, z_step)
            assert z_fields.size == n_depths

    metainfo = dict(
        num_fields=n_fields,
        num_channels=n_channels,
        num_planes=n_depths,
        num_frames=n_frames,
        num_rois=roi,
        x_pos=None,
        y_pos=None,
        z_pos=None,
        frame_rate=framerate,
        bidirectional=bidirectional_scan,
        bidirectional_z=bidirection_z,
        scan_datetime=scan_datetime,
        usecs_per_line=usec_per_line,
        scan_duration=total_duration,
        height_in_pixels=px_height,
        width_in_pixels=px_width,
        height_in_um=um_height,
        width_in_um=um_width,
        fieldX=x_field,
        fieldY=y_field,
        fieldZ=z_fields,
        recording_time=record_start_time,
    )

    return metainfo
