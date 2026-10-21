# Drill-rod handler: what the checks changed

`examples/rod_handler.py` builds one mechanism and then runs the checks. This note is the iteration those checks forced. The numbers are from `examples/renders/rod_handler_report.json` on this design. The earlier failures are the runs that preceded the geometry now in the script.

The arm is a slew base, a fabricated upper link, a forearm, and a jaw that closes on NQ and HQ core rod (about 70 to 89 mm outside diameter). HQ in the model is 88.9 mm outside, 77.8 mm inside. A 900 mm length of that tube is in the jaw and weighs 10.27 kg. A full 3 m rod weighs 34.22 kg, so the jaw carries a 23.96 kg payload on top of the visible tube. Everything is steel at 7.85 g/cm³. The bushings are steel too: the parts catalog stops at a 6001 bearing, and there is no bronze grade. That overstates the bushing mass by a small amount and does not change the loads that mattered.

A cylinder relation keeps each rod eye on its clevis. The joint tree stays a tree. At the stored pose the shoulder eye and the elbow eye both miss the clevis by 0.0 mm. The shoulder cylinder closes at 228.5 mm pin to pin and strokes 134.4 mm. The elbow closes at 288.9 mm and strokes 140.6 mm. The slew is a revolute, a hydraulic motor, not a cylinder. The jaw is a barrel and a rod whose stroke is the joint limit, 0 to 64 mm, which takes the seat from clear of the rod down onto the rod center.

## The first eye was a buried rib

The feature check flags an added solid when at least half of it was already inside the part. A trial lug put a rib through the middle of the eye boss and then drilled the bore. The rib is `eye_rib` on the part `eye_coupon`, and it is the only redundant feature that part reports. The production upper link uses the same check. Its eyes stand proud of the 8 mm side plates, and the redundant list is empty. The coupon stays in the file so the before is still reproducible.

The same coupon, 6 kN on a 3 mm tang, solves at a minimum safety factor of 0.73 and a peak of 344 MPa. That is a fail against a target of 2. It is why the production clevises are forks welded into the side plates rather than a thin tang.

## The cylinder would not fit, then it drove through the base

Pin-to-pin length has to hold a piston that stays inside the barrel at full stroke and an eye that stays outside the barrel at full retract. The first shoulder mounts could not. The closed length was about 176 mm and the travel was about 140 mm, so the eye would have entered the tube. The clevises moved to a closed length of 229 mm and a travel of 134 mm, with the tube ending 8 mm short of the eye and the piston still inside the barrel at full extend.

With the shoulder pin low on the turret, the under-slung cylinder at the reach pose passed through the pedestal. The pin is now 200 mm above the slew face, the boom cylinder is a 40 mm bore with a 52 mm tube, and the reach pose (shoulder +18°) is inside the joint stops of −40° to +24°. The lift motion, four frames from that reach to shoulder −36° and elbow −28°, reports no collisions. The rod origin goes from −84 mm to 991 mm.

## Hoses

The first routes ran close to the steel. The shoulder hose pinched on 7 samples and the elbow hose on 13, and the elbow hose stretched by 9.6%, past its 2% limit. Bend radius was not the problem (77 mm and 150 mm, against a 48 mm limit).

The routes that ship leave the turret outboard, above the cheeks, and come into a port boss partway along the barrel. The shoulder hose then passes: minimum bend 128 mm, no pinches. The elbow hose still grew by 4.0% at full reach, which is a hose cut too short, not a path through the steel. Setting the rest length to 2% over the longest pose in the lift puts both routes under the strain limit, with bends of 128 mm and 64 mm and no pinches.

## The slew bolts

A stuck rod is an 8 kN sideways snatch at the jaw, about 1.15 m from the slew center. Eight bolts share it. On a 130 mm circle that is 8846 N of shear per bolt.

Eight M12-8.8 bolts, dry, 22 mm grip, intense vibration: slip margin 0.57, loosening high, fail. Stepping the grade up does not create enough clamp to hold that shear. The pattern that holds is eight M20-10.9 on a 165 mm circle, preload fraction 0.75, wedge-locking washers: shear 6970 N, slip margin 3.26, loosening low, pass. The base plate is 420 × 400 × 22 mm with that bolt circle, and the fasteners are catalog socket-head M20-2.5 × 60.

## The upper link was at yield

The power estimate reports joint torque. Cylinder force is that torque divided by the moment arm. On this arm the shoulder peaks at 404 N·m. The worst moment arm over the shoulder travel is 74.6 mm, so the cylinder force is 5414 N. The elbow peaks at 144 N·m on a 22.9 mm arm, 6301 N. Both are applied along the cylinder at the angle where the arm is shortest, plus 500 N downward at the elbow pin (a 3 m rod is about 340 N; 500 N is the margin). The shoulder bore is fixed.

The first production link, with thin clevis ears, peaked at 450 MPa, safety factor 0.56. Thick forks welded into the side plates moved the peak but did not clear it: at both a 8 mm mesh and a 5 mm mesh the peak stayed at 245 MPa, safety factor 1.02, on the side plate just outside the shoulder eye (about x = 30 mm, y = 32 mm). That was the same number at two mesh sizes, so it was not a coarse-mesh spike. A doubler on the inside of each side plate, around the shoulder eye, is the change in the file now. The same load case peaks at 60.0 MPa, safety factor 4.16, displacement 0.22 mm. The peak sits on the top edge of the side plate, near the elbow clevis.

Pressures on a 40 mm bore are 6.18 MPa at the shoulder (149 W) and 7.19 MPa at the elbow (55 W). The jaw cylinder reports 0.004 MPa. That is the weight of the jaw, not a grip force. The model does not include the force required to hold the rod.

## Drill speed

A 3 m HQ tube, fixed at the handler end and free at the other, has a first bending frequency of 9.50 Hz in both directions. A drill at 600 rpm is 10 Hz. The separation is 5%, inside the 10% margin the check uses, so the report says resonance. The suggestion from the solver is to stiffen or to change the running speed. The operating fix is the second support the rig already has: the chuck. The handler should not be the only thing holding a 3 m rod while it spins.

With the chuck taking the other end, the free length is the stick-up past the handler. At 1.5 m the first mode is 37.85 Hz, which is 8% above the third harmonic of 700 rpm (35 Hz) and still flagged. At 1.2 m the first mode is 58.98 Hz, and 400, 600, and 700 rpm and their first three harmonics all sit outside the margin.

The upper link's first mode, before the root doubler, was 31.6 Hz, within 5% of the third harmonic of 600 rpm (30 Hz). After the doubler it is 70.6 Hz, and the same drill speeds are clear. Joint compliance would lower that number. The check says so.

## The jaw was floating off the forearm

At the reach pose the jaw barrel is a child of the forearm, and the viewport mesh uses that same solved transform. The solids did not meet. The barrel sat 95 mm off the cradle, so the red jaw read as a separate part past the orange arm. A post on the inner end of the cradle, clear of the HQ rod, and a bridge that stops 0.5 mm short of the barrel close that gap. The jaw still slides 64 mm onto the rod. Both poses report every child on its parent.

## Robot export

The export writes a URDF and a MuJoCo file, 29 links and 8 moving joints. MuJoCo loaded the file and took 5 steps. Cylinder relations are comments in the robot file. A simulator will not keep the rod eye on the clevis unless something else does. PyBullet is not installed here, so that check was skipped and the URDF was still written.
