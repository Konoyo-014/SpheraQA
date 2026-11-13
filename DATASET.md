# SpheraQA data

SpheraQA contains 22,197 evaluation questions associated with 7,686 complete FOA recordings. A recording can support several questions. The dataset provides the waveform, question, reference answer and scoring specification needed to evaluate a model's response.

## Recording conditions and question families

| Subset | Recordings | Questions | Content |
| --- | ---: | ---: | --- |
| `spatial_scene` | 7,038 | 20,469 | Spatial perception, source relations, identity and position, distance changes, and geometric reasoning |
| `controlled_reasoning` | 432 | 1,296 | Source selection followed by yaw, elevation measurement or comparison with a specified bearing |
| `height_selection` | 216 | 432 | Selection by Cartesian height followed by azimuth reporting |

The three levels describe what a question asks the model to do. L1 asks for spatial attributes such as direction, distance, coordinates, source count or a trajectory. L2 asks for relations between sources or the listener's turn. L3 combines source identification or selection with another operation, such as a rotation, a comparison or a change over time.

All waveforms have four channels at 16 kHz, ordered **W, Y, Z, X** under ACN/SN3D. The listener faces positive X; positive Y is left and positive Z is up. Azimuth is 0° in front, +90° to the left, −90° to the right and ±180° behind. Elevation is 0° on the horizontal plane and positive above it. Distances are in metres.

### Controlled reasoning

These recordings combine AISHELL speech and FSD50K environmental sounds in scenes with two, three or four virtual emitters. The 432 recordings cover each source count equally, with 216 static and 216 moving scenes; speech, environmental and mixed content each appear in 144 scenes. Each recording supplies three questions. The four task families each contain 324 questions:

| Task | Required operation |
| --- | --- |
| `spatial_rank_then_yaw` | Select a source by radial distance, then calculate its azimuth after a listener rotation |
| `onset_then_yaw` | Identify the first reference sound heard in the main window, then calculate its rotated azimuth |
| `height_rank_then_elevation` | Select a source by Cartesian height, then report its elevation |
| `elevation_rank_then_bearing` | Select a source by elevation, then calculate its shortest azimuthal distance from a specified bearing |

Each complete waveform starts with isolated, numbered reference sounds, followed by the main spatial mixture. The question gives the reference intervals, main-window start, trigger times and source-level convention. Reference sounds supply a one-metre-equivalent pressure reference. The same source signals and output gain are used in the main mixture, with signals repeating every 1.6 seconds. Direct-path propagation includes travel-time delay and inverse-distance pressure attenuation. All query times in these questions refer to the complete recording, including the reference sounds.

Reference answers are calculated from Cartesian source trajectories. An independent implementation checks source selection and the resulting geometry; complete waveforms are also checked against the declared propagation model. Questions that require source selection or an additional spatial operation are constructed so that the checked wrong-source and omitted-operation results fall outside the correct answer's scoring interval. These checks concern the specified physical model and operations.

### Height selection

This subset contains 72 groups of three related recordings, with two questions per recording. A `block_id` identifies the three views:

| `view` | Physical change |
| --- | --- |
| `base` | The reference geometry |
| `radius` | Source distances change while their direction rays are preserved |
| `elevation` | Source directions rotate while radial trajectories and the complete omnidirectional W signal are preserved |

Each question selects the highest or lowest emitter by Cartesian height and asks for its azimuth. Height and elevation are distinct: height also depends on distance. The paired views let researchers compare responses when either distance or elevation changes the height ordering. The 216 recordings include 108 static and 108 moving views, with two, three and four emitters represented equally. Full waveforms and spatial reference values have been checked by independent reconstruction.

The generated subsets share 24 groups of source recordings. Their `source_group_id` values identify those groups across both subsets. Use recording, block or source-group units as appropriate when estimating variability; multiple questions from the same recording are related observations.

## Question fields

Each line of `spheraqa.jsonl` is one JSON object.

| Field | Meaning |
| --- | --- |
| `id` | Unique question identifier, used to join model responses with questions |
| `audio_path` | WAV path relative to the repository root |
| `recording_id` | SHA-256 of the complete WAV file |
| `question` | Text presented to the model, including the required coordinate and output conventions |
| `answer` | Reference answer |
| `level` | `L1`, `L2` or `L3` |
| `task` | Question family |
| `split` | `test`; all supplied questions belong to the evaluation set |
| `subset` | One of the three recording conditions described above |
| `scoring` | Required answer components and their comparison rules |
| `source_group_id` | Present for generated recordings; groups questions that use the same source-recording group |
| `block_id`, `view` | Present for height-selection questions; identify related physical views |
| `source_count`, `motion`, `content` | Source count, motion and sound-content labels where available |

`recording_id` identifies a waveform. `source_group_id` instead identifies a group of original sound ingredients that can appear in several generated waveforms. `block_id` identifies the three related height-selection views. These fields describe different units and should be used according to the analysis being performed.

`make_prompts.py` exports only the question ID, audio path, question text and audio-format specification. Feed the question and actual waveform to the model. Reference answers, scoring fields and grouping information are used for evaluation and analysis.

## Media and scoring

`media.jsonl` records the WAV path, SHA-256, byte size, frame count, duration, sample rate and channel convention. `audio_parts.json` lists the independent archives containing those files. `prepare_audio.py` verifies their contents before completing extraction.

The scoring specification in each question defines the requested numerical, categorical or semantic components. Azimuth is compared on a circle; elevation, distance and shortest angular separation use absolute error. The standard tolerances are 20°, 10°, 1 m and 20°, respectively. An answer must satisfy every requested component to receive one point. Trajectory answers must cover the requested time points and any explicit time range, with 0.051 seconds allowed for timestamp rounding.

For L3, `gpt-4o-2024-11-20` extracts numerical or categorical claims from the model's final answer without receiving reference values for that extraction. The code checks quoted evidence and computes numerical errors. Source identities are matched from the original final answer in the context of the question. Required semantic explanations are compared with their reference propositions. Every supplied L3 answer is processed by the specified model.

The [reference result](examples/summary.json) reports SpheraAudio's accuracy on all 1,825 L3 questions. [Model settings](examples/model_metadata.json), original responses, individual decisions and recorded GPT replies accompany it. `replay_example.py` recomputes the decisions from those replies with network access disabled.
